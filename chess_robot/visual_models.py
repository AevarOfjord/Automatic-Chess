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
    white_elo: int = DEFAULT_WHITE_ELO
    black_elo: int = DEFAULT_BLACK_ELO
    white_skill: int = DEFAULT_WHITE_SKILL
    black_skill: int = DEFAULT_BLACK_SKILL
    move_time_s: float = DEFAULT_MOVE_TIME_S


@dataclass
class SimulatorStats:
    game_number: int = 1
    plies: int = 0
    completed_transfers: int = 0
    last_move: str = "waiting"
    mode: str = "initializing"
    message: str = "Space pauses, N steps, R resets, +/- changes speed"
