from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ArmId(str, Enum):
    WHITE = "WHITE"
    BLACK = "BLACK"

    @property
    def opposite(self) -> "ArmId":
        return ArmId.BLACK if self is ArmId.WHITE else ArmId.WHITE


@dataclass(frozen=True)
class ArmConfig:
    base_x_mm: float
    base_y_mm: float
    forward_angle_deg: float
    link_1_mm: float = 300.0
    link_2_mm: float = 300.0
    shoulder_limits_deg: tuple[float, float] = (-175.0, 175.0)
    elbow_limits_deg: tuple[float, float] = (-170.0, 170.0)
    fixed_tool_z_mm: float = 0.0
    park_x_mm: float = 200.0
    park_y_mm: float = -30.0


@dataclass(frozen=True)
class RobotConfig:
    board_origin_x_mm: float = 100.0
    board_origin_y_mm: float = 0.0
    square_size_mm: float = 50.0
    board_squares: int = 8
    table_columns: int = 12
    table_rows: int = 8
    serial_port: str = "COM3"
    serial_baudrate: int = 115200
    response_timeout_s: float = 20.0
    command_retries: int = 1
    max_wire_bytes: int = 240
    journal_path: Path = Path("runtime_data/command_journal.jsonl")
    white_arm: ArmConfig = field(
        default_factory=lambda: ArmConfig(
            base_x_mm=300.0,
            base_y_mm=-120.0,
            forward_angle_deg=90.0,
            park_x_mm=300.0,
            park_y_mm=-30.0,
        )
    )
    black_arm: ArmConfig = field(
        default_factory=lambda: ArmConfig(
            base_x_mm=300.0,
            base_y_mm=520.0,
            forward_angle_deg=-90.0,
            park_x_mm=300.0,
            park_y_mm=430.0,
        )
    )

    @property
    def board_size_mm(self) -> float:
        return self.square_size_mm * self.board_squares

    @property
    def table_width_mm(self) -> float:
        return self.square_size_mm * self.table_columns

    @property
    def table_height_mm(self) -> float:
        return self.square_size_mm * self.table_rows

    def arm(self, arm_id: ArmId) -> ArmConfig:
        return self.white_arm if arm_id is ArmId.WHITE else self.black_arm
