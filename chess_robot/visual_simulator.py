from __future__ import annotations

import argparse
import math
import random
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


class VisualChessRobotSimulator:
    """Top-down digital twin for the dual-SCARA chess robot.

    The simulator deliberately reuses the production geometry, inventory, and
    chess move planner.  The only mocked part is time: instead of sending
    trajectories to ESP32s, it interpolates the same pickup/drop intent on
    screen.
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
        for arm in ArmId:
            park = self.layout.park(arm)
            cfg = self.config.arm(arm)
            pose = JointPose(cfg.home_shoulder_deg, cfg.home_elbow_deg)
            self.arms[arm] = VisualArm(
                arm_id=arm,
                tool=park,
                pose=pose,
                z_mm=self.config.arm(arm).fixed_tool_z_mm,
                target_label="folded home",
            )

    def close(self) -> None:
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

    def set_speed(self, speed: float) -> None:
        self.options.speed = max(0.1, min(8.0, float(speed)))
        self.stats.message = f"Speed {self.options.speed:0.2f}×"

    def nudge_speed(self, factor: float) -> None:
        self.set_speed(self.options.speed * factor)

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
            self.stats.message = "Already at the starting position"
            return
        self.plan_queue.clear()
        self.active_step = None
        self.game_over_delay_s = 0.0
        self._try_queue_reset()
        self.stats.message = "Reset requested"

    def _queue_next_plan(self) -> None:
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

        player_name = self._player_name_for_turn()
        preferred_move = self._player_for_turn().choose_move(self.board)
        candidate_moves = [preferred_move] + [
            move for move in self.board.legal_moves if move != preferred_move
        ]
        skipped = 0
        for move in candidate_moves:
            plan = self.move_planner.plan(self.board, self.inventory, move)
            try:
                self._enqueue_plan(plan, is_reset=False)
            except TrajectoryPlanningError:
                skipped += 1
                continue
            self.pending_plan = plan
            self.pending_plan_is_reset = False
            try:
                san = self.board.san(move)
            except ValueError:
                san = move.uci()
            self.stats.last_move = move.uci()
            self.stats.last_move_san = san
            self.stats.path_skips += skipped
            side = "White" if self.board.turn else "Black"
            self.stats.mode = f"{side} to move"
            self.stats.message = self._describe_move(move)
            if skipped:
                self.stats.message = f"{self.stats.message} ({skipped} blocked move(s) skipped)"
            if player_name:
                self.stats.message = f"{player_name}: {self.stats.message}"
            return
        self.stats.mode = "trajectory fault"
        self.stats.message = "No legal chess move has a collision-free puck route"
        raise TrajectoryPlanningError(self.stats.message)

    def _try_queue_reset(self) -> None:
        try:
            plan = self.reset_planner.plan_pathable(self.inventory, self.puck_planner)
            self._enqueue_plan(plan, is_reset=True)
        except (RuntimeError, TrajectoryPlanningError) as exc:
            self.pending_plan = None
            self.pending_plan_is_reset = False
            self.plan_queue.clear()
            self.current_plan_paths.clear()
            self.paused = True
            self.stats.mode = "reset trajectory fault"
            self.stats.message = f"Reset needs blocker clearing: {exc}"
            return
        self.pending_plan = plan
        self.pending_plan_is_reset = True

    def _enqueue_plan(self, plan: MovePlan, *, is_reset: bool) -> None:
        plan_queue: list[AnimationStep] = []
        planned_paths: list[PlannedPath] = []
        virtual_tools = {arm_id: arm.tool for arm_id, arm in self.arms.items()}
        physical_state = self.inventory.clone()
        for transfer in plan.transfers:
            # Keep-out: opposite arm returns to its folded home before this arm works.
            opposite = transfer.arm.opposite
            park = self.layout.park(opposite)
            if self._distance(virtual_tools[opposite], park) > 1.0:
                fixed_z = self.config.arm(opposite).fixed_tool_z_mm
                plan_queue.append(
                    self._home_motion(opposite, "keep-out folded home", virtual_tools[opposite], park, fixed_z)
                )
                virtual_tools[opposite] = park
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
        home_pose = JointPose(cfg.home_shoulder_deg, cfg.home_elbow_deg)
        joint_distance = 180.0
        if current_pose is not None:
            joint_distance = abs(home_pose.shoulder_deg - current_pose.shoulder_deg) + abs(
                home_pose.elbow_deg - current_pose.elbow_deg
            )
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
                )
                arm.tool = self.forward_kinematics(step.arm, arm.pose)[2]
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
                self.stats.moves_san.append(san)
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
        piece = self.board.piece_at(move.from_square)
        piece_name = piece.symbol().upper() if piece else "?"
        capture = " capture" if self.board.is_capture(move) else ""
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

    def forward_kinematics(self, arm_id: ArmId, pose: JointPose | None) -> tuple[Point, Point, Point]:
        cfg = self.config.arm(arm_id)
        base = Point(cfg.base_x_mm, cfg.base_y_mm)
        if pose is None:
            return base, base, self.arms[arm_id].tool
        shoulder = math.radians(pose.shoulder_deg)
        elbow = math.radians(pose.elbow_deg)
        local_elbow = Point(cfg.link_1_mm * math.cos(shoulder), cfg.link_1_mm * math.sin(shoulder))
        local_tool = Point(
            local_elbow.x_mm + cfg.link_2_mm * math.cos(shoulder + elbow),
            local_elbow.y_mm + cfg.link_2_mm * math.sin(shoulder + elbow),
        )
        orientation = math.radians(cfg.forward_angle_deg)

        def rotate(local: Point) -> Point:
            return Point(
                base.x_mm + math.cos(orientation) * local.x_mm - math.sin(orientation) * local.y_mm,
                base.y_mm + math.sin(orientation) * local.x_mm + math.cos(orientation) * local.y_mm,
            )

        return base, rotate(local_elbow), rotate(local_tool)


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
    simulator = VisualChessRobotSimulator(options=options)
    PygameRenderer(simulator).run()
