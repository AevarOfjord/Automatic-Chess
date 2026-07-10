from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
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
    # Each motor has one continuous 270-degree travel window. Values are
    # motor-space angles; the kinematics layer expands geometric IK angles by
    # +/-360 degrees to find an equivalent pose inside these windows.
    shoulder_limits_deg: tuple[float, float] = (-135.0, 135.0)
    elbow_limits_deg: tuple[float, float] = (-70.0, 200.0)
    joint_limit_margin_deg: float = 15.0
    singularity_margin_deg: float = 15.0
    # A deliberately singular stowed pose used only while the opposite arm is
    # active. Motion to/from it is explicit; normal table motion still keeps
    # the safety margin above.
    # With the mirrored 60/-120 degree base headings, -60 stores both folded
    # link pairs parallel to the long table edge, entirely outside the board.
    home_shoulder_deg: float = -60.0
    home_elbow_deg: float = -180.0
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
    # The arm is stationary at the source/destination while the electromagnet
    # engages or releases. These conservative defaults are tuned during first
    # hardware bring-up and are sent to the arm controller with each command.
    magnet_pickup_settle_s: float = 0.5
    magnet_release_settle_s: float = 0.5
    max_wire_bytes: int = 240
    journal_path: Path = Path("runtime_data/command_journal.jsonl")
    # Certified mirrored geometry from ``optimize-geometry``. Base centers
    # are exactly 50mm beyond the table edge. The 270mm links cover every
    # usable grid center and adjacent-grid route with >=15 degrees of
    # operational reserve; the separate folded home pose is used only while
    # waiting for the opposite arm.
    white_arm: ArmConfig = field(
        default_factory=lambda: ArmConfig(
            base_x_mm=0.0,
            base_y_mm=-250.0,
            forward_angle_deg=60.0,
            link_1_mm=270.0,
            link_2_mm=270.0,
            shoulder_limits_deg=(-135.0, 135.0),
            elbow_limits_deg=(-345.0, -75.0),
            park_x_mm=0.0,
            park_y_mm=-250.0,
        )
    )
    black_arm: ArmConfig = field(
        default_factory=lambda: ArmConfig(
            base_x_mm=0.0,
            base_y_mm=250.0,
            forward_angle_deg=-120.0,
            link_1_mm=270.0,
            link_2_mm=270.0,
            shoulder_limits_deg=(-135.0, 135.0),
            elbow_limits_deg=(-345.0, -75.0),
            park_x_mm=0.0,
            park_y_mm=250.0,
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

    @property
    def table_origin_x_mm(self) -> float:
        """World x of the table's left edge (column C1) — table is x-centered on 0."""
        return -self.table_width_mm / 2.0

    @property
    def table_origin_y_mm(self) -> float:
        """World y of the table's bottom edge (row R1) — table is y-centered on 0."""
        return -self.table_height_mm / 2.0

    def arm(self, arm_id: ArmId) -> ArmConfig:
        return self.white_arm if arm_id is ArmId.WHITE else self.black_arm

    @classmethod
    def from_env(cls, base: "RobotConfig | None" = None) -> "RobotConfig":
        """Overlay serial/runtime settings from environment variables.

        Supported:
          CHESS_ROBOT_PORT, CHESS_ROBOT_BAUD, CHESS_ROBOT_TIMEOUT_S,
          CHESS_ROBOT_RETRIES, CHESS_ROBOT_PICKUP_SETTLE_S,
          CHESS_ROBOT_RELEASE_SETTLE_S, CHESS_ROBOT_JOURNAL
        """

        config = base or cls()
        port = os.environ.get("CHESS_ROBOT_PORT")
        baud = os.environ.get("CHESS_ROBOT_BAUD")
        timeout = os.environ.get("CHESS_ROBOT_TIMEOUT_S")
        retries = os.environ.get("CHESS_ROBOT_RETRIES")
        pickup_settle = os.environ.get("CHESS_ROBOT_PICKUP_SETTLE_S")
        release_settle = os.environ.get("CHESS_ROBOT_RELEASE_SETTLE_S")
        journal = os.environ.get("CHESS_ROBOT_JOURNAL")
        updates: dict[str, object] = {}
        if port:
            updates["serial_port"] = port
        if baud:
            updates["serial_baudrate"] = int(baud)
        if timeout:
            updates["response_timeout_s"] = float(timeout)
        if retries:
            updates["command_retries"] = int(retries)
        if pickup_settle:
            updates["magnet_pickup_settle_s"] = max(0.0, float(pickup_settle))
        if release_settle:
            updates["magnet_release_settle_s"] = max(0.0, float(release_settle))
        if journal:
            updates["journal_path"] = Path(journal)
        return replace(config, **updates) if updates else config
