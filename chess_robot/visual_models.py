from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import ArmId
from .geometry import JointPose, Point

from .game import (
    DEFAULT_BLACK_ELO,
    DEFAULT_BLACK_SKILL,
    DEFAULT_MOVE_TIME_S,
    DEFAULT_WHITE_ELO,
    DEFAULT_WHITE_SKILL,
)


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
    # Extra width on the right for the dashboard panel (screen-space overlay).
    width: int = 1540
    height: int = 1000
    # Y range covers the arm bases at +/-310mm plus room for their labels;
    # X stays at the table's own extent since base_x is only +/-40mm.
    world_min_x: float = -390.0
    world_max_x: float = 390.0
    world_min_y: float = -410.0
    world_max_y: float = 410.0
    margin: int = 28
    # Dashboard occupies this many pixels on the right edge of the window.
    # The board render is height-bound (see `scale`), so up to ~540px here
    # comes out of otherwise-dead horizontal margin, not board size.
    dashboard_width: int = 520

    @property
    def board_area_width(self) -> int:
        """Pixels available for the top-down twin (excludes dashboard strip)."""
        return max(200, self.width - self.dashboard_width - self.margin)

    @property
    def scale(self) -> float:
        sx = (self.board_area_width - self.margin) / (self.world_max_x - self.world_min_x)
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
    magnet_on: bool = False
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
    start_pose: JointPose | None = None
    end_pose: JointPose | None = None


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
    white_elo: int = DEFAULT_WHITE_ELO
    black_elo: int = DEFAULT_BLACK_ELO
    white_skill: int = DEFAULT_WHITE_SKILL
    black_skill: int = DEFAULT_BLACK_SKILL
    move_time_s: float = DEFAULT_MOVE_TIME_S
    # Control-board options
    auto_loop: bool = True  # after game over, auto-reset and continue
    show_paths: bool = True
    show_cell_labels: bool = True


@dataclass
class SimulatorStats:
    game_number: int = 1
    plies: int = 0
    completed_transfers: int = 0
    last_move: str = "waiting"
    last_move_san: str = "—"
    mode: str = "initializing"
    message: str = "Use the control board or keyboard (Space / N / R / +/-)"
    last_result: str = ""
    moves_san: list[str] | None = None
    path_skips: int = 0
    active_arm: str = "—"
    active_step_label: str = "idle"
    plan_transfers_total: int = 0
    plan_transfers_done: int = 0

    def __post_init__(self) -> None:
        if self.moves_san is None:
            self.moves_san = []
