from __future__ import annotations

import argparse
import math
import random
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import chess

from .config import ArmId, RobotConfig
from .game import EngineProfile, Player, RandomPlayer, StockfishPlayer
from .geometry import BoardLayout, JointPose, Point, ScaraKinematics
from .inventory import PhysicalInventory
from .planning import ChessMovePlanner, MovePlan, PhysicalTransfer, ResetPlanner
from .trajectory import PlannedPath, PuckTrajectoryPlanner, TrajectoryPlanningError
from .visual_models import (
    AnimationStep,
    SimulatorStats,
    VisualArm,
    VisualOptions,
    Viewport,
)
from .visual_render import PygameRenderer

# Re-export models for callers and tests that import from this module.
__all__ = [
    "VisualChessRobotSimulator",
    "VisualOptions",
    "Viewport",
    "PygameRenderer",
    "run_visual_simulator",
]


@dataclass(frozen=True)
class _BackgroundPlanResult:
    """Result of engine + path planning computed off the UI thread."""

    generation: int
    kind: str  # "move" | "reset" | "reset_fault" | "move_fault"
    plan: MovePlan | None = None
    paths: tuple[PlannedPath, ...] = ()
    is_reset: bool = False
    skipped: int = 0
    player_name: str = ""
    message: str = ""
    mode: str = ""
    last_move: str = ""
    last_move_san: str = ""
    error: str = ""


class VisualChessRobotSimulator:
    """Top-down digital twin for the dual-arm chess robot.

    The simulator reuses production geometry, inventory, and chess move
    planning. Stockfish search and puck path planning run on a background
    worker so the pygame UI stays responsive during think time.
    """

    def __init__(
        self,
        config: RobotConfig | None = None,
        options: VisualOptions | None = None,
    ) -> None:
        self.config = config or RobotConfig()
        self.options = options or VisualOptions()
        self.layout = BoardLayout(self.config)
        self.kinematics = {arm: ScaraKinematics(self.config.arm(arm)) for arm in ArmId}
        self.board = chess.Board()
        self.inventory = PhysicalInventory()
        self.move_planner = ChessMovePlanner()
        self.reset_planner = ResetPlanner()
        self.puck_planner = PuckTrajectoryPlanner(self.config, self.layout)
        self.random = random.Random(self.options.seed)
        self.players = self._create_players()
        self.stats = SimulatorStats()
        self.paused = not self.options.auto_start
        self.step_once = False
        self.game_over_delay_s = 0.0
        self.plan_queue: list[AnimationStep] = []
        self.active_step: AnimationStep | None = None
        self.pending_plan: MovePlan | None = None
        self.pending_plan_is_reset = False
        self.current_plan_paths: list[PlannedPath] = []
        self.current_locations = dict(self.inventory.locations)
        self.arms: dict[ArmId, VisualArm] = {}
        # Single worker: Stockfish UCI engines are not shared concurrently.
        self._plan_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sim-plan")
        self._plan_future: Future[_BackgroundPlanResult] | None = None
        self._plan_generation = 0
        for arm in ArmId:
            cfg = self.config.arm(arm)
            pose = JointPose(cfg.home_shoulder_deg, cfg.home_elbow_deg, cfg.home_wrist_deg)
            tool = ScaraKinematics(cfg).forward(pose)[-1]
            self.arms[arm] = VisualArm(
                arm_id=arm,
                tool=tool,
                pose=pose,
                z_mm=cfg.fixed_tool_z_mm,
                target_label="folded home",
            )

    def close(self) -> None:
        self._invalidate_background_plan()
        self._plan_executor.shutdown(wait=False, cancel_futures=True)
        for player in self.players:
            player.close()

    def tick(self, dt_s: float) -> None:
        if self.paused and not self.step_once:
            return
        dt_s *= max(0.05, self.options.speed)
        if self.active_step:
            self._advance_step(dt_s)
            return
        if self.plan_queue:
            self._start_next_step()
            return
        if self.pending_plan:
            self._commit_pending_plan()
            if self.step_once:
                self.step_once = False
                self.paused = True
            return
        # Apply finished engine/path jobs before the inter-game delay so a
        # reset plan can start animating as soon as it is ready.
        if self._poll_background_plan():
            return
        if self.game_over_delay_s > 0:
            self.game_over_delay_s = max(0.0, self.game_over_delay_s - dt_s)
            return
        self._queue_next_plan()

    def request_single_step(self) -> None:
        self.step_once = True
        self.paused = False
        self.stats.message = "Stepping one planned move"

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self.stats.mode = "paused"
            self.stats.message = "Paused — press Play or Step"
        else:
            self.stats.mode = "running"
            self.stats.message = "Running"

    def pause(self) -> None:
        self.paused = True
        self.stats.mode = "paused"
        self.stats.message = "Paused — press Play or Step"

    def resume(self) -> None:
        self.paused = False
        self.step_once = False
        self.stats.mode = "running"
        self.stats.message = "Running"

    # Discrete animation speed steps used by the control board and +/- keys.
    SPEED_PRESETS: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0)

    def set_speed(self, speed: float) -> None:
        target = float(speed)
        # Snap to the nearest allowed step (0.5 / 1 / 2 / 5 / 10).
        self.options.speed = min(self.SPEED_PRESETS, key=lambda step: abs(step - target))
        self.stats.message = f"Speed {self.options.speed:g}×"

    def nudge_speed(self, factor: float) -> None:
        """Move one step up (factor > 1) or down (factor < 1) through SPEED_PRESETS."""
        steps = self.SPEED_PRESETS
        index = min(range(len(steps)), key=lambda i: abs(steps[i] - self.options.speed))
        if factor > 1.0:
            index = min(len(steps) - 1, index + 1)
        else:
            index = max(0, index - 1)
        self.set_speed(steps[index])

    def toggle_auto_loop(self) -> None:
        self.options.auto_loop = not self.options.auto_loop
        state = "ON" if self.options.auto_loop else "OFF"
        self.stats.message = f"Auto-loop after game: {state}"

    def toggle_show_paths(self) -> None:
        self.options.show_paths = not self.options.show_paths

    def toggle_show_labels(self) -> None:
        self.options.show_cell_labels = not self.options.show_cell_labels

    def skip_animation(self) -> None:
        """Jump through the current plan's animation without waiting."""
        # Finish any in-flight engine/path job so there is something to skip.
        if self._plan_future is not None:
            try:
                self._plan_future.result(timeout=30.0)
            except Exception:  # noqa: BLE001
                pass
            self._poll_background_plan()
        guard = 0
        while guard < 5000 and (self.active_step or self.plan_queue or self.pending_plan):
            guard += 1
            if self.active_step:
                step = self.active_step
                arm = self.arms[step.arm]
                arm.tool = step.end
                arm.z_mm = step.end_z
                if step.end_pose is not None:
                    arm.pose = step.end_pose
                else:
                    reach = self.kinematics[step.arm].inverse(arm.tool, arm.pose)
                    if reach.reachable:
                        arm.pose = reach.pose
                if step.on_end:
                    step.on_end()
                self.active_step = None
                continue
            if self.plan_queue:
                self._start_next_step()
                continue
            if self.pending_plan:
                self._commit_pending_plan()
                if self.step_once:
                    self.step_once = False
                    self.paused = True
                break
        self.stats.message = "Skipped current animation"

    def reset_now(self) -> None:
        if self.board == chess.Board() and not self.plan_queue and not self.active_step:
            if self._plan_future is None:
                self.stats.message = "Already at the starting position"
                return
        self._invalidate_background_plan()
        self.plan_queue.clear()
        self.active_step = None
        self.pending_plan = None
        self.pending_plan_is_reset = False
        self.game_over_delay_s = 0.0
        self._try_queue_reset()
        self.stats.message = "Reset requested"

    def _invalidate_background_plan(self) -> None:
        """Drop any in-flight plan so a later result is ignored."""
        self._plan_generation += 1
        self._plan_future = None

    def _poll_background_plan(self) -> bool:
        """Apply a finished background plan. True if still waiting or just applied."""
        future = self._plan_future
        if future is None:
            return False
        if not future.done():
            # Keep the UI loop alive while Stockfish / path planning runs.
            # Yield the GIL so the pure-Python planner thread can progress
            # (tight tick loops otherwise starve the worker).
            if "thinking" not in self.stats.mode and "planning" not in self.stats.mode:
                self.stats.mode = "engine thinking"
                self.stats.message = "Thinking / planning paths…"
                self.stats.active_step_label = "background plan"
            time.sleep(0.001)
            return True
        self._plan_future = None
        try:
            result = future.result()
        except Exception as exc:  # noqa: BLE001 — surface planner/engine faults in the UI
            self.paused = True
            self.stats.mode = "planning fault"
            self.stats.message = f"Background plan failed: {exc}"
            return True
        if result.generation != self._plan_generation:
            return False
        self._apply_background_result(result)
        return True

    def _apply_background_result(self, result: _BackgroundPlanResult) -> None:
        if result.kind == "move_fault":
            self.paused = True
            self.stats.mode = result.mode or "trajectory fault"
            self.stats.message = result.message or "No pathable move"
            return
        if result.kind == "reset_fault":
            self.pending_plan = None
            self.pending_plan_is_reset = False
            self.plan_queue.clear()
            self.current_plan_paths.clear()
            self.paused = True
            self.stats.mode = result.mode or "reset trajectory fault"
            self.stats.message = result.message
            return
        if result.plan is None:
            self.paused = True
            self.stats.mode = "planning fault"
            self.stats.message = result.message or "Empty plan result"
            return
        if result.kind == "move":
            self.stats.path_skips += result.skipped
            self.stats.last_move = result.last_move
            self.stats.last_move_san = result.last_move_san
            self.stats.mode = result.mode
            self.stats.message = result.message
        self._enqueue_plan(
            result.plan,
            is_reset=result.is_reset,
            precomputed_paths=list(result.paths),
        )
        self.pending_plan = result.plan
        self.pending_plan_is_reset = result.is_reset

    def _queue_next_plan(self) -> None:
        if self._plan_future is not None:
            return
        if self.options.max_plies is not None and self.stats.plies >= self.options.max_plies:
            self.stats.mode = "max plies reached"
            self.stats.message = "Max plies reached"
            if self.options.auto_loop:
                self.game_over_delay_s = 2.0
                self._try_queue_reset()
            else:
                self.paused = True
                self.stats.message = "Max plies — press Reset / Next game or enable Auto-loop"
            return

        if self.board.is_game_over(claim_draw=True):
            result = self.board.result(claim_draw=True)
            self.stats.last_result = result
            self.stats.mode = f"game over {result}"
            if self.options.auto_loop:
                self.stats.message = "Game over — auto-resetting for next loop"
                self.game_over_delay_s = 1.2
                self._try_queue_reset()
            else:
                self.paused = True
                self.stats.message = f"Game over {result} — press Next game or enable Auto-loop"
            return

        generation = self._plan_generation
        board = self.board.copy(stack=False)
        inventory = self.inventory.clone()
        player = self._player_for_turn()
        player_name = self._player_name_for_turn()
        self.stats.mode = "engine thinking"
        self.stats.message = f"{player_name}: thinking…"
        self.stats.active_step_label = "engine"
        self._plan_future = self._plan_executor.submit(
            self._worker_plan_move,
            generation,
            board,
            inventory,
            player,
            player_name,
        )

    def _try_queue_reset(self) -> None:
        if self._plan_future is not None and not self._plan_future.done():
            # A newer generation already invalidated the old job.
            pass
        generation = self._plan_generation
        inventory = self.inventory.clone()
        self.stats.mode = "planning reset"
        self.stats.message = "Planning pathable board reset…"
        self.stats.active_step_label = "reset plan"
        self._plan_future = self._plan_executor.submit(
            self._worker_plan_reset,
            generation,
            inventory,
        )

    def _worker_plan_move(
        self,
        generation: int,
        board: chess.Board,
        inventory: PhysicalInventory,
        player: Player,
        player_name: str,
    ) -> _BackgroundPlanResult:
        preferred_move = player.choose_move(board)
        candidate_moves = [preferred_move] + [
            move for move in board.legal_moves if move != preferred_move
        ]
        skipped = 0
        for move in candidate_moves:
            plan = self.move_planner.plan(board, inventory, move)
            try:
                paths = self._path_plan_transfers(plan, inventory)
            except TrajectoryPlanningError:
                skipped += 1
                continue
            try:
                san = board.san(move)
            except ValueError:
                san = move.uci()
            side = "White" if board.turn else "Black"
            message = self._describe_move_on_board(board, move)
            if skipped:
                message = f"{message} ({skipped} blocked move(s) skipped)"
            if player_name:
                message = f"{player_name}: {message}"
            return _BackgroundPlanResult(
                generation=generation,
                kind="move",
                plan=plan,
                paths=tuple(paths),
                is_reset=False,
                skipped=skipped,
                player_name=player_name,
                message=message,
                mode=f"{side} to move",
                last_move=move.uci(),
                last_move_san=san,
            )
        return _BackgroundPlanResult(
            generation=generation,
            kind="move_fault",
            message="No legal chess move has a collision-free puck route",
            mode="trajectory fault",
        )

    def _worker_plan_reset(
        self, generation: int, inventory: PhysicalInventory
    ) -> _BackgroundPlanResult:
        try:
            plan = self.reset_planner.plan_pathable(inventory, self.puck_planner)
            paths = self._path_plan_transfers(plan, inventory)
        except (RuntimeError, TrajectoryPlanningError) as exc:
            return _BackgroundPlanResult(
                generation=generation,
                kind="reset_fault",
                message=f"Reset needs blocker clearing: {exc}",
                mode="reset trajectory fault",
            )
        return _BackgroundPlanResult(
            generation=generation,
            kind="reset",
            plan=plan,
            paths=tuple(paths),
            is_reset=True,
            message=f"Reset plan: {len(plan.transfers)} piece transfers",
            mode="resetting board",
            last_move_san="reset",
        )

    def _path_plan_transfers(
        self, plan: MovePlan, inventory: PhysicalInventory
    ) -> list[PlannedPath]:
        """Validate every transfer has a collision-free puck path (worker-safe)."""
        physical_state = inventory.clone()
        paths: list[PlannedPath] = []
        for transfer in plan.transfers:
            paths.append(
                self.puck_planner.plan_transfer(
                    physical_state,
                    transfer.token_id,
                    transfer.source,
                    transfer.destination,
                )
            )
            physical_state.move(transfer.token_id, transfer.destination)
        return paths

    def _enqueue_plan(
        self,
        plan: MovePlan,
        *,
        is_reset: bool,
        precomputed_paths: list[PlannedPath] | None = None,
    ) -> None:
        plan_queue: list[AnimationStep] = []
        planned_paths: list[PlannedPath] = []
        virtual_tools = {arm_id: arm.tool for arm_id, arm in self.arms.items()}
        physical_state = self.inventory.clone()
        for index, transfer in enumerate(plan.transfers):
            # Keep-out: opposite arm returns to its folded home before this arm works.
            opposite = transfer.arm.opposite
            park = self.layout.park(opposite)
            if self._distance(virtual_tools[opposite], park) > 1.0:
                fixed_z = self.config.arm(opposite).fixed_tool_z_mm
                plan_queue.append(
                    self._home_motion(opposite, "keep-out folded home", virtual_tools[opposite], park, fixed_z)
                )
                virtual_tools[opposite] = park
            if precomputed_paths is not None:
                planned_path = precomputed_paths[index]
            else:
                planned_path = self.puck_planner.plan_transfer(
                    physical_state,
                    transfer.token_id,
                    transfer.source,
                    transfer.destination,
                )
            planned_paths.append(planned_path)
            plan_queue.extend(
                self._steps_for_transfer(transfer, virtual_tools[transfer.arm], planned_path.points)
            )
            physical_state.move(transfer.token_id, transfer.destination)
            virtual_tools[transfer.arm] = self.layout.location(transfer.destination)
        plan_queue.extend(self._parking_steps(virtual_tools))
        self.plan_queue = plan_queue
        self.current_plan_paths = planned_paths
        self.stats.plan_transfers_total = len(plan.transfers)
        self.stats.plan_transfers_done = 0
        if is_reset:
            self.stats.mode = "resetting board"
            self.stats.message = f"Reset plan: {len(plan.transfers)} piece transfers"
            self.stats.last_move_san = "reset"
        elif plan.move:
            self.stats.message = f"Move {plan.move.uci()}: {len(plan.transfers)} physical transfer(s)"

    def _steps_for_transfer(
        self, transfer: PhysicalTransfer, start_tool: Point, carry_path: list[Point]
    ) -> list[AnimationStep]:
        arm = self.arms[transfer.arm]
        source = self.layout.location(transfer.source)
        destination = self.layout.location(transfer.destination)
        fixed_z = self.config.arm(transfer.arm).fixed_tool_z_mm
        token_id = transfer.token_id

        def pick() -> None:
            arm.held_token_id = token_id
            self.current_locations.pop(token_id, None)

        def drop() -> None:
            self.current_locations[token_id] = transfer.destination
            arm.held_token_id = None
            self.stats.completed_transfers += 1
            self.stats.plan_transfers_done += 1

        def magnet_on() -> None:
            arm.magnet_on = True

        def magnet_off() -> None:
            arm.magnet_on = False

        steps = [
            self._motion(
                transfer.arm,
                f"{transfer.reason}: approach source",
                start_tool,
                source,
                fixed_z,
                fixed_z,
            ),
            AnimationStep(
                transfer.arm,
                "magnet on / pickup",
                source,
                source,
                fixed_z,
                fixed_z,
                self.config.magnet_pickup_settle_s,
                on_begin=magnet_on,
                on_end=pick,
            ),
        ]
        for index, (a, b) in enumerate(zip(carry_path, carry_path[1:]), start=1):
            steps.append(self._motion(transfer.arm, f"planar carry {index}", a, b, fixed_z, fixed_z))
        steps.append(
            AnimationStep(
                transfer.arm,
                "magnet off / release",
                destination,
                destination,
                fixed_z,
                fixed_z,
                self.config.magnet_release_settle_s,
                on_begin=magnet_off,
                on_end=drop,
            )
        )
        return steps

    def _parking_steps(self, virtual_tools: dict[ArmId, Point]) -> list[AnimationStep]:
        steps: list[AnimationStep] = []
        for arm_id, arm in self.arms.items():
            park = self.layout.park(arm_id)
            fixed_z = self.config.arm(arm_id).fixed_tool_z_mm
            start = virtual_tools[arm_id]
            if self._distance(start, park) > 1.0:
                steps.append(self._home_motion(arm_id, "folded home", start, park, fixed_z))
        return steps

    def _home_motion(
        self, arm_id: ArmId, label: str, start: Point, end: Point, fixed_z: float
    ) -> AnimationStep:
        cfg = self.config.arm(arm_id)
        current_pose = self.arms[arm_id].pose
        home_pose = JointPose(cfg.home_shoulder_deg, cfg.home_elbow_deg, cfg.home_wrist_deg)
        joint_distance = 180.0
        if current_pose is not None:
            joint_distance = home_pose.joint_distance(current_pose)
        return AnimationStep(
            arm_id,
            label,
            start,
            end,
            fixed_z,
            fixed_z,
            max(0.2, joint_distance / 240.0),
            end_pose=home_pose,
        )

    def _motion(
        self,
        arm_id: ArmId,
        label: str,
        start: Point,
        end: Point,
        start_z: float,
        end_z: float,
    ) -> AnimationStep:
        distance = self._distance(start, end)
        duration = max(0.12, distance / 320.0)
        return AnimationStep(arm_id, label, start, end, start_z, end_z, duration)

    def _start_next_step(self) -> None:
        self.active_step = self.plan_queue.pop(0)
        arm = self.arms[self.active_step.arm]
        arm.target_label = self.active_step.label
        self.stats.active_arm = self.active_step.arm.value
        self.stats.active_step_label = self.active_step.label
        if self.active_step.on_begin:
            self.active_step.on_begin()

    def _advance_step(self, dt_s: float) -> None:
        step = self.active_step
        if step is None:
            return
        step.elapsed_s += dt_s
        t = min(1.0, step.elapsed_s / max(step.duration_s, 0.001))
        eased = t * t * (3.0 - 2.0 * t)
        arm = self.arms[step.arm]
        if step.end_pose is not None:
            if step.start_pose is None:
                step.start_pose = arm.pose
            if step.start_pose is not None:
                arm.pose = JointPose(
                    step.start_pose.shoulder_deg
                    + (step.end_pose.shoulder_deg - step.start_pose.shoulder_deg) * eased,
                    step.start_pose.elbow_deg
                    + (step.end_pose.elbow_deg - step.start_pose.elbow_deg) * eased,
                    step.start_pose.wrist_deg
                    + (step.end_pose.wrist_deg - step.start_pose.wrist_deg) * eased,
                )
                arm.tool = self.forward_kinematics(step.arm, arm.pose)[-1]
            else:
                arm.tool = step.end
        else:
            arm.tool = Point(
                step.start.x_mm + (step.end.x_mm - step.start.x_mm) * eased,
                step.start.y_mm + (step.end.y_mm - step.start.y_mm) * eased,
            )
        arm.z_mm = step.start_z + (step.end_z - step.start_z) * eased
        if step.end_pose is None:
            reach = self.kinematics[step.arm].inverse(arm.tool, arm.pose)
            if reach.reachable:
                arm.pose = reach.pose
        if t >= 1.0:
            arm.tool = step.end
            arm.z_mm = step.end_z
            if step.end_pose is not None:
                arm.pose = step.end_pose
            if step.on_end:
                step.on_end()
            self.active_step = None

    def _commit_pending_plan(self) -> None:
        if self.pending_plan is None:
            return
        self.inventory = self.pending_plan.resulting_inventory
        self.current_locations = dict(self.inventory.locations)
        if self.pending_plan_is_reset:
            self.board = chess.Board()
            self.stats.plies = 0
            self.stats.game_number += 1
            self.stats.last_move = "reset complete"
            self.stats.last_move_san = "—"
            self.stats.moves_san = []
            self.stats.moves_uci = []
            self.stats.path_skips = 0
            self.stats.mode = f"game {self.stats.game_number}"
            self.stats.message = "Board reset; continuing the loop"
            self.stats.active_arm = "—"
            self.stats.active_step_label = "idle"
            self.stats.plan_transfers_total = 0
            self.stats.plan_transfers_done = 0
        else:
            move = self.pending_plan.move
            if move is not None:
                try:
                    # SAN must be taken from the pre-push board; expected_board is post-move.
                    prior = self.board
                    san = prior.san(move)
                except ValueError:
                    san = move.uci()
                assert self.stats.moves_san is not None
                assert self.stats.moves_uci is not None
                self.stats.moves_san.append(san)
                self.stats.moves_uci.append(move.uci())
                self.stats.last_move_san = san
            self.board = self.pending_plan.expected_board
            self.stats.plies += 1
            if self.board.is_game_over(claim_draw=True):
                self.stats.last_result = self.board.result(claim_draw=True)
        self.pending_plan = None
        self.pending_plan_is_reset = False

    def token_at_screen_location(self) -> dict[str, str]:
        return {location: token_id for token_id, location in self.current_locations.items()}

    def next_dead_slot_summary(self) -> str:
        labels: list[str] = []
        for arm in ArmId:
            try:
                labels.append(self.inventory.first_empty_dead_label(arm))
            except RuntimeError:
                labels.append(f"{arm.value[0]} full")
        return " / ".join(labels)

    def _create_players(self) -> tuple[Player, Player]:
        if not self.options.use_engine:
            return (
                RandomPlayer(self.options.seed),
                RandomPlayer(None if self.options.seed is None else self.options.seed + 1),
            )
        return (
            StockfishPlayer(
                self.options.engine_path,
                EngineProfile(
                    "Stockfish White",
                    self.options.white_elo,
                    self.options.white_skill,
                    self.options.move_time_s,
                ),
            ),
            StockfishPlayer(
                self.options.engine_path,
                EngineProfile(
                    "Stockfish Black",
                    self.options.black_elo,
                    self.options.black_skill,
                    self.options.move_time_s,
                ),
            ),
        )

    def _player_for_turn(self) -> Player:
        white_index = (self.stats.game_number - 1) % 2
        return self.players[white_index if self.board.turn is chess.WHITE else 1 - white_index]

    def _player_name_for_turn(self) -> str:
        player = self._player_for_turn()
        profile = getattr(player, "profile", None)
        if profile is None:
            return "Random"
        return getattr(profile, "name", "Stockfish")

    def _describe_move(self, move: chess.Move) -> str:
        return self._describe_move_on_board(self.board, move)

    @staticmethod
    def _describe_move_on_board(board: chess.Board, move: chess.Move) -> str:
        piece = board.piece_at(move.from_square)
        piece_name = piece.symbol().upper() if piece else "?"
        capture = " capture" if board.is_capture(move) else ""
        promotion = (
            f" promotes to {chess.piece_symbol(move.promotion).upper()}" if move.promotion else ""
        )
        return (
            f"{piece_name} {chess.square_name(move.from_square)}→"
            f"{chess.square_name(move.to_square)}{capture}{promotion}"
        )

    @staticmethod
    def _distance(a: Point, b: Point) -> float:
        return math.hypot(a.x_mm - b.x_mm, a.y_mm - b.y_mm)

    def forward_kinematics(
        self, arm_id: ArmId, pose: JointPose | None
    ) -> tuple[Point, Point, Point, Point]:
        """Return base, elbow joint, wrist joint, and tool in world mm."""
        cfg = self.config.arm(arm_id)
        base = Point(cfg.base_x_mm, cfg.base_y_mm)
        if pose is None:
            tool = self.arms[arm_id].tool
            return base, base, base, tool
        return ScaraKinematics(cfg).forward(pose)


def run_visual_simulator(args: argparse.Namespace | None = None) -> None:
    options = VisualOptions()
    if args is not None:
        options.seed = getattr(args, "seed", None)
        options.max_plies = getattr(args, "max_plies", None)
        options.speed = getattr(args, "speed", 1.0)
        options.auto_start = not getattr(args, "paused", False)
        options.fps = getattr(args, "fps", 60)
        options.use_engine = not getattr(args, "random", False)
        options.engine_path = getattr(args, "engine", "stockfish.exe")
        options.white_elo = getattr(args, "white_elo", options.white_elo)
        options.black_elo = getattr(args, "black_elo", options.black_elo)
        options.white_skill = getattr(args, "white_skill", options.white_skill)
        options.black_skill = getattr(args, "black_skill", options.black_skill)
        options.move_time_s = getattr(args, "move_time", options.move_time_s)
        options.fullscreen = bool(getattr(args, "fullscreen", False))
        width = getattr(args, "width", None)
        height = getattr(args, "height", None)
        if width is not None:
            options.window_width = max(800, int(width))
        if height is not None:
            options.window_height = max(500, int(height))
    simulator = VisualChessRobotSimulator(options=options)
    PygameRenderer(simulator).run()
