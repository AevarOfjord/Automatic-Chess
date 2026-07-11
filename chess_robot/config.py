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
    """Planar 3R arm geometry for MG995-class 180° servos.

    Joints are relative angles (shoulder, elbow, wrist). Each motor has one
    continuous 180° travel window; the kinematics layer expands geometric IK
    angles by ±360° to find an equivalent pose inside those windows.
    """

    base_x_mm: float
    base_y_mm: float
    forward_angle_deg: float
    link_1_mm: float = 200.0
    link_2_mm: float = 160.0
    link_3_mm: float = 180.0
    shoulder_limits_deg: tuple[float, float] = (-90.0, 90.0)
    elbow_limits_deg: tuple[float, float] = (0.0, 180.0)
    wrist_limits_deg: tuple[float, float] = (0.0, 180.0)
    # Servo end-stops are hard; keep a small operational reserve inside the
    # 180° window so commanded targets never ride the mechanical limit.
    joint_limit_margin_deg: float = 5.0
    singularity_margin_deg: float = 5.0
    # Folded rest used only while the opposite arm is active. Elbow/wrist at
    # the far end of their 180° windows zigzag the three links outside the
    # board. With base heading 45° / −135°, shoulder −45° puts the fold
    # exactly parallel to the long table edge (world ±X, y = base y).
    home_shoulder_deg: float = -45.0
    home_elbow_deg: float = 180.0
    home_wrist_deg: float = 180.0
    fixed_tool_z_mm: float = 0.0
    park_x_mm: float = 200.0
    park_y_mm: float = -30.0


@dataclass(frozen=True)
class RobotConfig:
    # 14-column grid: W rack | empty gap | chess 8 | empty gap | B rack.
    # board_origin_x is offset from the table left edge to file-a edge
    # (2 dead cols + 1 separator = 150 mm).
    board_origin_x_mm: float = 150.0
    board_origin_y_mm: float = 0.0
    square_size_mm: float = 50.0
    board_squares: int = 8
    table_columns: int = 14
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
    # Certified mirrored 3-link geometry for MG995 180° servos. Base centers
    # sit 50 mm beyond the table edge. Unequal links (200 / 160 / 180 mm)
    # cover every usable grid center and adjacent-grid route with >=5° of
    # operational reserve; the folded home pose is used only while waiting
    # for the opposite arm.
    white_arm: ArmConfig = field(
        default_factory=lambda: ArmConfig(
            base_x_mm=0.0,
            base_y_mm=-250.0,
            # 45° heading covers the wider 14-column table with 200/160/180 links.
            forward_angle_deg=45.0,
            link_1_mm=200.0,
            link_2_mm=160.0,
            link_3_mm=180.0,
            shoulder_limits_deg=(-90.0, 90.0),
            elbow_limits_deg=(0.0, 180.0),
            wrist_limits_deg=(0.0, 180.0),
            park_x_mm=0.0,
            park_y_mm=-250.0,
        )
    )
    black_arm: ArmConfig = field(
        default_factory=lambda: ArmConfig(
            base_x_mm=0.0,
            base_y_mm=250.0,
            forward_angle_deg=-135.0,
            link_1_mm=200.0,
            link_2_mm=160.0,
            link_3_mm=180.0,
            shoulder_limits_deg=(-90.0, 90.0),
            elbow_limits_deg=(0.0, 180.0),
            wrist_limits_deg=(0.0, 180.0),
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
