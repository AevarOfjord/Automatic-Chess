from __future__ import annotations

import argparse
import math
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import chess

from .config import ArmId, RobotConfig
from .game import EngineProfile, Player, RandomPlayer, StockfishPlayer
from .geometry import BoardLayout, JointPose, Point, ScaraKinematics
from .inventory import PhysicalInventory
from .planning import ChessMovePlanner, MovePlan, PhysicalTransfer, ResetPlanner
from .trajectory import PlannedPath, PuckTrajectoryPlanner, TrajectoryPlanningError


PIECE_SYMBOLS = {
    "P": "♙",
    "N": "♘",
    "B": "♗",
    "R": "♖",
    "Q": "♕",
    "K": "♔",
}


@dataclass
class Viewport:
    width: int = 1220
    height: int = 920
    world_min_x: float = -90.0
    world_max_x: float = 690.0
    world_min_y: float = -190.0
    world_max_y: float = 590.0
    margin: int = 28

    @property
    def scale(self) -> float:
        sx = (self.width - self.margin * 2) / (self.world_max_x - self.world_min_x)
        sy = (self.height - self.margin * 2) / (self.world_max_y - self.world_min_y)
        return min(sx, sy)

    def screen(self, point: Point) -> tuple[int, int]:
        x = self.margin + (point.x_mm - self.world_min_x) * self.scale
        y = self.margin + (self.world_max_y - point.y_mm) * self.scale
        return round(x), round(y)

    def length(self, mm: float) -> int:
        return max(1, round(mm * self.scale))


@dataclass
class VisualArm:
    arm_id: ArmId
    tool: Point
    pose: JointPose | None = None
    z_mm: float = 0.0
    held_token_id: str | None = None
    target_label: str = "parked"


@dataclass
class AnimationStep:
    arm: ArmId
    label: str
    start: Point
    end: Point
    start_z: float
    end_z: float
    duration_s: float
    on_begin: Callable[[], None] | None = None
    on_end: Callable[[], None] | None = None
    elapsed_s: float = 0.0


@dataclass
class VisualOptions:
    seed: int | None = None
    max_plies: int | None = None
    opening: bool = True
    speed: float = 1.0
    auto_start: bool = True
    fps: int = 60
    use_engine: bool = False
    engine_path: str | Path = "stockfish.exe"
    white_elo: int = 1700
    black_elo: int = 1450
    white_skill: int = 10
    black_skill: int = 6
    move_time_s: float = 0.08


@dataclass
class SimulatorStats:
    game_number: int = 1
    plies: int = 0
    completed_transfers: int = 0
    last_move: str = "waiting"
    mode: str = "initializing"
    message: str = "Space pauses, N steps, R resets, +/- changes speed"


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
            pose = self.kinematics[arm].inverse(park).pose
            self.arms[arm] = VisualArm(
                arm_id=arm,
                tool=park,
                pose=pose,
                z_mm=self.config.arm(arm).fixed_tool_z_mm,
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

    def toggle_pause(self) -> None:
        self.paused = not self.paused
        if self.paused:
            self.stats.mode = "paused"

    def reset_now(self) -> None:
        if self.board == chess.Board() and not self.plan_queue and not self.active_step:
            self.stats.message = "Already at the starting position"
            return
        self.plan_queue.clear()
        self.active_step = None
        self._try_queue_reset()

    def _queue_next_plan(self) -> None:
        if self.options.max_plies is not None and self.stats.plies >= self.options.max_plies:
            self.stats.mode = "max plies reached"
            self.game_over_delay_s = 2.0
            self._try_queue_reset()
            return

        if self.board.is_game_over(claim_draw=True):
            result = self.board.result(claim_draw=True)
            self.stats.mode = f"game over {result}"
            self.stats.message = "Resetting board for the next loop"
            self.game_over_delay_s = 1.2
            self._try_queue_reset()
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
            self.stats.last_move = move.uci()
            self.stats.mode = f"{'White' if self.board.turn else 'Black'} to move"
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
        plan = self.reset_planner.plan(self.inventory)
        try:
            self._enqueue_plan(plan, is_reset=True)
        except TrajectoryPlanningError as exc:
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
        if is_reset:
            self.stats.mode = "resetting board"
            self.stats.message = f"Reset plan: {len(plan.transfers)} piece transfers"
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

        steps = [
            self._motion(transfer.arm, f"{transfer.reason}: approach source", start_tool, source, fixed_z, fixed_z),
            AnimationStep(transfer.arm, "magnet on / pickup", source, source, fixed_z, fixed_z, 0.18, on_begin=pick),
        ]
        for index, (a, b) in enumerate(zip(carry_path, carry_path[1:]), start=1):
            steps.append(self._motion(transfer.arm, f"planar carry {index}", a, b, fixed_z, fixed_z))
        steps.append(
            AnimationStep(transfer.arm, "magnet off / release", destination, destination, fixed_z, fixed_z, 0.18, on_end=drop)
        )
        return steps

    def _parking_steps(self, virtual_tools: dict[ArmId, Point]) -> list[AnimationStep]:
        steps: list[AnimationStep] = []
        for arm_id, arm in self.arms.items():
            park = self.layout.park(arm_id)
            fixed_z = self.config.arm(arm_id).fixed_tool_z_mm
            start = virtual_tools[arm_id]
            if self._distance(start, park) > 1.0:
                steps.append(self._motion(arm_id, "park", start, park, arm.z_mm, fixed_z))
        return steps

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
        arm.tool = Point(
            step.start.x_mm + (step.end.x_mm - step.start.x_mm) * eased,
            step.start.y_mm + (step.end.y_mm - step.start.y_mm) * eased,
        )
        arm.z_mm = step.start_z + (step.end_z - step.start_z) * eased
        reach = self.kinematics[step.arm].inverse(arm.tool, arm.pose)
        if reach.reachable:
            arm.pose = reach.pose
        if t >= 1.0:
            arm.tool = step.end
            arm.z_mm = step.end_z
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
            self.stats.mode = f"game {self.stats.game_number}"
            self.stats.message = "Board reset; continuing the loop"
        else:
            self.board = self.pending_plan.expected_board
            self.stats.plies += 1
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
                EngineProfile("Stockfish White", self.options.white_elo, self.options.white_skill, self.options.move_time_s),
            ),
            StockfishPlayer(
                self.options.engine_path,
                EngineProfile("Stockfish Black", self.options.black_elo, self.options.black_skill, self.options.move_time_s),
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
        promotion = f" promotes to {chess.piece_symbol(move.promotion).upper()}" if move.promotion else ""
        return f"{piece_name} {chess.square_name(move.from_square)}→{chess.square_name(move.to_square)}{capture}{promotion}"

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


class PygameRenderer:
    def __init__(self, simulator: VisualChessRobotSimulator, viewport: Viewport | None = None) -> None:
        import pygame

        self.pygame = pygame
        self.sim = simulator
        self.viewport = viewport or Viewport()
        pygame.init()
        self.screen = pygame.display.set_mode((self.viewport.width, self.viewport.height))
        pygame.display.set_caption("Dual-SCARA Chess Robot Digital Twin")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("segoeui", 18)
        self.small = pygame.font.SysFont("segoeui", 14)
        self.piece_font = pygame.font.SysFont("segoeuisymbol", 25)

    def run(self) -> None:
        pygame = self.pygame
        running = True
        try:
            while running:
                dt_s = self.clock.tick(self.sim.options.fps) / 1000.0
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_q):
                            running = False
                        elif event.key == pygame.K_SPACE:
                            self.sim.toggle_pause()
                        elif event.key == pygame.K_n:
                            self.sim.request_single_step()
                        elif event.key == pygame.K_r:
                            self.sim.reset_now()
                        elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                            self.sim.options.speed = min(8.0, self.sim.options.speed * 1.25)
                        elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                            self.sim.options.speed = max(0.1, self.sim.options.speed / 1.25)
                self.sim.tick(dt_s)
                self.draw()
                pygame.display.flip()
        finally:
            self.sim.close()
            pygame.quit()

    def draw(self) -> None:
        pygame = self.pygame
        self.screen.fill((24, 27, 33))
        self._draw_workspace()
        self._draw_storage()
        self._draw_board()
        self._draw_planned_paths()
        self._draw_pieces()
        self._draw_arms()
        self._draw_panel()

    def _draw_workspace(self) -> None:
        pygame = self.pygame
        board_rect = self._rect_from_world(Point(-45, -155), Point(645, 555))
        pygame.draw.rect(self.screen, (35, 39, 48), board_rect, border_radius=18)
        pygame.draw.rect(self.screen, (68, 75, 90), board_rect, width=2, border_radius=18)
        for arm in ArmId:
            base = Point(self.sim.config.arm(arm).base_x_mm, self.sim.config.arm(arm).base_y_mm)
            pygame.draw.circle(self.screen, (81, 92, 116), self.viewport.screen(base), self.viewport.length(18))
            label = "White robot base" if arm is ArmId.WHITE else "Black robot base"
            self._label(label, base, (180, 190, 210), dy=-28 if arm is ArmId.WHITE else 24)

    def _draw_board(self) -> None:
        pygame = self.pygame
        colors = ((236, 220, 188), (110, 145, 112))
        size = self.sim.config.square_size_mm
        chess_start_col = round(self.sim.config.board_origin_x_mm / size)
        chess_end_col = chess_start_col + self.sim.config.board_squares
        for row_from_bottom in range(self.sim.config.table_rows):
            for table_col in range(self.sim.config.table_columns):
                world = Point(table_col * size, row_from_bottom * size)
                rect = self._rect_from_world(world, Point(world.x_mm + size, world.y_mm + size))
                if chess_start_col <= table_col < chess_end_col:
                    chess_file = table_col - chess_start_col
                    pygame.draw.rect(self.screen, colors[(row_from_bottom + chess_file) % 2], rect)
                    if row_from_bottom == 0:
                        self._tiny(chr(ord("a") + chess_file), Point(world.x_mm + size / 2, -14), (180, 190, 210))
                    if table_col == chess_start_col:
                        self._tiny(str(row_from_bottom + 1), Point(world.x_mm - 14, world.y_mm + size / 2), (180, 190, 210))
                else:
                    pygame.draw.rect(self.screen, (50, 55, 66), rect)
                    pygame.draw.rect(self.screen, (70, 77, 92), rect, width=1)
                pygame.draw.circle(
                    self.screen,
                    (88, 96, 112),
                    self.viewport.screen(Point(world.x_mm + size / 2, world.y_mm + size / 2)),
                    self.viewport.length(3),
                )
                physical_col = table_col + 1
                physical_row = self.sim.config.table_rows - row_from_bottom
                self._tiny(
                    f"{physical_col},{physical_row}",
                    Point(world.x_mm + size / 2, world.y_mm + size - 9),
                    (118, 128, 145),
                )
        table_outline = self._rect_from_world(Point(0, 0), Point(self.sim.config.table_width_mm, self.sim.config.table_height_mm))
        chess_outline = self._rect_from_world(
            Point(self.sim.config.board_origin_x_mm, 0),
            Point(self.sim.config.board_origin_x_mm + self.sim.config.board_size_mm, self.sim.config.board_size_mm),
        )
        pygame.draw.rect(self.screen, (18, 20, 25), table_outline, width=3)
        pygame.draw.rect(self.screen, (18, 20, 25), chess_outline, width=3)
        for table_col in range(self.sim.config.table_columns):
            self._tiny(
                str(table_col + 1),
                Point((table_col + 0.5) * size, self.sim.config.table_height_mm + 18),
                (210, 218, 230),
            )
        for row_from_top in range(self.sim.config.table_rows):
            y = self.sim.config.table_height_mm - (row_from_top + 0.5) * size
            self._tiny(str(row_from_top + 1), Point(-18, y), (210, 218, 230))

    def _draw_storage(self) -> None:
        pygame = self.pygame
        for arm in ArmId:
            for index in range(16):
                p = self.sim.layout.dead_slot(arm, index)
                pygame.draw.circle(self.screen, (69, 51, 57), self.viewport.screen(p), self.viewport.length(14), width=2)
                label_dx = -28 if arm is ArmId.WHITE else 28
                self._tiny(self.sim.layout.dead_slot_label(arm, index), Point(p.x_mm + label_dx, p.y_mm), (210, 190, 170))
            self._label(
                f"{arm.value.title()} dead-piece line",
                Point(50 if arm is ArmId.WHITE else 550, 430),
                (170, 180, 200),
                dy=-8,
            )

    def _draw_planned_paths(self) -> None:
        pygame = self.pygame
        for path in self.sim.current_plan_paths[:6]:
            if len(path.points) < 2:
                continue
            screen_points = [self.viewport.screen(point) for point in path.points]
            pygame.draw.lines(self.screen, (245, 221, 111), False, screen_points, width=2)
            for point in screen_points[1:-1]:
                pygame.draw.circle(self.screen, (245, 221, 111), point, self.viewport.length(4))

    def _draw_pieces(self) -> None:
        pygame = self.pygame
        occupied = self.sim.token_at_screen_location()
        held = {arm.held_token_id for arm in self.sim.arms.values() if arm.held_token_id}
        for token_id, location in self.sim.current_locations.items():
            if token_id in held:
                continue
            try:
                point = self.sim.layout.location(location)
            except ValueError:
                continue
            token = self.sim.inventory.tokens[token_id]
            piece_type = self._display_piece_type(token.piece_type, location)
            self._draw_piece(point, piece_type, token.color, ghost=not location.startswith("board:"))
        for arm in self.sim.arms.values():
            if arm.held_token_id:
                token = self.sim.inventory.tokens[arm.held_token_id]
                self._draw_piece(arm.tool, token.piece_type, token.color, held=True)

    def _display_piece_type(self, token_piece_type: str, location: str) -> str:
        if not location.startswith("board:"):
            return token_piece_type
        square_name = location.split(":", 1)[1]
        piece = self.sim.board.piece_at(chess.parse_square(square_name))
        return piece.symbol().upper() if piece else token_piece_type

    def _draw_piece(self, point: Point, piece_type: str, color: ArmId, *, ghost: bool = False, held: bool = False) -> None:
        pygame = self.pygame
        center = self.viewport.screen(point)
        radius = self.viewport.length(16 if not ghost else 11)
        fill = (245, 241, 230) if color is ArmId.WHITE else (34, 35, 39)
        outline = (235, 197, 95) if held else ((50, 55, 65) if color is ArmId.WHITE else (220, 220, 220))
        if ghost:
            fill = tuple(max(0, c - 25) for c in fill)
        pygame.draw.circle(self.screen, fill, center, radius)
        pygame.draw.circle(self.screen, outline, center, radius, width=2)
        text_color = (18, 20, 24) if color is ArmId.WHITE else (245, 245, 245)
        symbol = PIECE_SYMBOLS.get(piece_type, piece_type)
        surface = self.piece_font.render(symbol, True, text_color)
        rect = surface.get_rect(center=center)
        self.screen.blit(surface, rect)

    def _draw_arms(self) -> None:
        pygame = self.pygame
        arm_colors = {ArmId.WHITE: (96, 167, 255), ArmId.BLACK: (255, 109, 109)}
        for arm_id, arm in self.sim.arms.items():
            base, elbow, tool = self.sim.forward_kinematics(arm_id, arm.pose)
            color = arm_colors[arm_id]
            points = [self.viewport.screen(base), self.viewport.screen(elbow), self.viewport.screen(tool)]
            pygame.draw.lines(self.screen, (18, 20, 24), False, points, width=12)
            pygame.draw.lines(self.screen, color, False, points, width=7)
            pygame.draw.circle(self.screen, color, points[1], self.viewport.length(10))
            pygame.draw.circle(self.screen, (245, 221, 111), self.viewport.screen(arm.tool), self.viewport.length(9))
            magnet_color = (88, 255, 180) if arm.held_token_id else (145, 150, 165)
            pygame.draw.circle(self.screen, magnet_color, self.viewport.screen(arm.tool), self.viewport.length(5))
            self._tiny("fixed height", Point(arm.tool.x_mm + 28, arm.tool.y_mm + 18), (210, 218, 230))

    def _draw_panel(self) -> None:
        pygame = self.pygame
        panel = pygame.Rect(self.viewport.width - 330, 26, 300, 230)
        pygame.draw.rect(self.screen, (16, 18, 24), panel, border_radius=14)
        pygame.draw.rect(self.screen, (72, 80, 98), panel, width=1, border_radius=14)
        lines = [
            "Dual-SCARA Chess Robot",
            f"Game: {self.sim.stats.game_number}",
            f"Ply: {self.sim.stats.plies}",
            f"Last move: {self.sim.stats.last_move}",
            f"Mode: {self.sim.stats.mode}",
            f"Transfers: {self.sim.stats.completed_transfers}",
            f"Speed: {self.sim.options.speed:0.2f}x",
            f"State: {'paused' if self.sim.paused else 'running'}",
            f"Next dead slots: {self.sim.next_dead_slot_summary()}",
            "",
            self.sim.stats.message,
        ]
        y = panel.y + 16
        for idx, line in enumerate(lines):
            font = self.font if idx == 0 else self.small
            color = (245, 247, 250) if idx == 0 else (190, 200, 215)
            for wrapped in self._wrap(line, 34):
                surface = font.render(wrapped, True, color)
                self.screen.blit(surface, (panel.x + 16, y))
                y += 22 if idx == 0 else 18

    def _rect_from_world(self, a: Point, b: Point):
        pygame = self.pygame
        x1, y1 = self.viewport.screen(a)
        x2, y2 = self.viewport.screen(b)
        return pygame.Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    def _label(self, text: str, point: Point, color: tuple[int, int, int], *, dy: int = 0) -> None:
        surface = self.small.render(text, True, color)
        rect = surface.get_rect(center=(self.viewport.screen(point)[0], self.viewport.screen(point)[1] + dy))
        self.screen.blit(surface, rect)

    def _tiny(self, text: str, point: Point, color: tuple[int, int, int]) -> None:
        surface = self.small.render(text, True, color)
        rect = surface.get_rect(center=self.viewport.screen(point))
        self.screen.blit(surface, rect)

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        if len(text) <= width:
            return [text]
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines


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
        options.white_elo = getattr(args, "white_elo", 1700)
        options.black_elo = getattr(args, "black_elo", 1450)
        options.white_skill = getattr(args, "white_skill", 10)
        options.black_skill = getattr(args, "black_skill", 6)
        options.move_time_s = getattr(args, "move_time", 0.08)
    simulator = VisualChessRobotSimulator(options=options)
    PygameRenderer(simulator).run()
