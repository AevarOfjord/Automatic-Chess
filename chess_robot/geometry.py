from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import chess

from .config import ArmConfig, ArmId, RobotConfig
from .inventory import DEAD_SLOTS_PER_ARM, dead_label


@dataclass(frozen=True)
class Point:
    x_mm: float
    y_mm: float


@dataclass(frozen=True)
class JointPose:
    """Relative joint angles for a planar 3R arm (shoulder, elbow, wrist)."""

    shoulder_deg: float
    elbow_deg: float
    wrist_deg: float = 0.0

    def as_wire(self, z_mm: float, speed: int = 2400, acceleration: int = 1200) -> list[float | int]:
        return [
            round(self.shoulder_deg, 3),
            round(self.elbow_deg, 3),
            round(self.wrist_deg, 3),
            round(z_mm, 2),
            speed,
            acceleration,
        ]

    def joint_distance(self, other: "JointPose") -> float:
        return (
            abs(self.shoulder_deg - other.shoulder_deg)
            + abs(self.elbow_deg - other.elbow_deg)
            + abs(self.wrist_deg - other.wrist_deg)
        )


@dataclass(frozen=True)
class Reachability:
    reachable: bool
    pose: JointPose | None
    reason: str = ""
    singularity_margin: float = 0.0


class BoardLayout:
    """Maps named chess/inventory locations into the shared millimetre frame.

    Piece cells are 50 mm. Thin empty separators sit between racks and the board
    (default 20 mm) and are **not** piece columns.

    Grid naming (0-based piece-cell indices → 1-based labels):

    - **Piece columns** ``C1…C12`` left → right among 50 mm cells only.
    - **Table rows** ``R1…R8`` bottom → top (+Y), same sense as chess ranks.
    - **White dead rack** ``C1–C2`` → ``W1…W16`` (fills top → bottom).
    - **20 mm gap** between white rack and chess.
    - **Chess** ``C3–C10`` = files ``a…h``, ranks ``1…8``.
    - **20 mm gap** between chess and black rack.
    - **Black dead rack** ``C11–C12`` → ``B1…B16``.
    """

    def __init__(self, config: RobotConfig):
        self.config = config

    @property
    def chess_start_col(self) -> int:
        """0-based piece-column index of chess file a."""
        return self.config.dead_rack_columns

    @property
    def chess_end_col(self) -> int:
        """0-based exclusive end piece-column of the chess area."""
        return self.chess_start_col + self.config.board_squares

    def column_label(self, table_col: int) -> str:
        """Human label for a 0-based piece column (``C1``…``C12``)."""
        if not 0 <= table_col < self.config.table_columns:
            raise ValueError(f"table column out of range: {table_col}")
        return f"C{table_col + 1}"

    def row_label(self, row_from_bottom: int) -> str:
        """Human label for a 0-based row from the bottom (``R1``…``R8``)."""
        if not 0 <= row_from_bottom < self.config.table_rows:
            raise ValueError(f"table row out of range: {row_from_bottom}")
        return f"R{row_from_bottom + 1}"

    def cell_center(self, table_col: int, row_from_bottom: int) -> Point:
        """World center of a 50 mm piece cell."""
        size = self.config.square_size_mm
        return Point(
            self.config.column_left_x_mm(table_col) + size / 2.0,
            self.config.table_origin_y_mm + (row_from_bottom + 0.5) * size,
        )

    def separator_center_x(self, *, left: bool) -> float:
        """World x of the midline of the left or right empty gap."""
        ox = self.config.table_origin_x_mm
        size = self.config.square_size_mm
        rack = self.config.dead_rack_columns
        sep = self.config.separator_width_mm
        if left:
            return ox + rack * size + sep / 2.0
        return ox + rack * size + sep + self.config.board_size_mm + sep / 2.0

    def chess_square_name(self, table_col: int, row_from_bottom: int) -> str | None:
        """Return ``a1``…``h8`` if this cell is on the playable board, else ``None``."""
        if not self.chess_start_col <= table_col < self.chess_end_col:
            return None
        if not 0 <= row_from_bottom < self.config.board_squares:
            return None
        file_index = table_col - self.chess_start_col
        return chess.square_name(chess.square(file_index, row_from_bottom))

    def dead_slot_at_cell(self, table_col: int, row_from_bottom: int) -> tuple[ArmId, int] | None:
        """Return ``(arm, 0-based index)`` if the cell is a dead-rack slot."""
        row_from_top = self.config.table_rows - 1 - row_from_bottom
        rack = self.config.dead_rack_columns
        # White rack: first rack columns. Black rack: last rack columns.
        if 0 <= table_col < rack:
            arm = ArmId.WHITE
            col_in_rack = table_col
        elif self.config.table_columns - rack <= table_col < self.config.table_columns:
            arm = ArmId.BLACK
            col_in_rack = table_col - (self.config.table_columns - rack)
        else:
            return None
        index = row_from_top * rack + col_in_rack
        if not 0 <= index < DEAD_SLOTS_PER_ARM:
            return None
        return arm, index

    def cell_label(self, table_col: int, row_from_bottom: int) -> str:
        """Primary display name for a piece cell (chess square or W/B rack id)."""
        chess_name = self.chess_square_name(table_col, row_from_bottom)
        if chess_name is not None:
            return chess_name
        dead = self.dead_slot_at_cell(table_col, row_from_bottom)
        if dead is not None:
            return dead_label(dead[0], dead[1])
        return f"{self.column_label(table_col)}{self.row_label(row_from_bottom)}"

    def square(self, square_name: str) -> Point:
        square = chess.parse_square(square_name)
        return Point(
            self.config.table_origin_x_mm
            + self.config.board_origin_x_mm
            + (chess.square_file(square) + 0.5) * self.config.square_size_mm,
            self.config.table_origin_y_mm
            + self.config.board_origin_y_mm
            + (chess.square_rank(square) + 0.5) * self.config.square_size_mm,
        )

    def park(self, arm: ArmId) -> Point:
        cfg = self.config.arm(arm)
        return Point(cfg.park_x_mm, cfg.park_y_mm)

    def dead_slot(self, arm: ArmId, index: int) -> Point:
        if not 0 <= index < DEAD_SLOTS_PER_ARM:
            raise ValueError(f"dead-piece slot out of range: {index}")
        # Fill order: W1/B1 at the top of the rack, two columns, left-to-right within a row.
        rack = self.config.dead_rack_columns
        row_from_top, col_in_rack = divmod(index, rack)
        table_col = (
            col_in_rack if arm is ArmId.WHITE else self.config.table_columns - rack + col_in_rack
        )
        row_from_bottom = self.config.table_rows - 1 - row_from_top
        return self.cell_center(table_col, row_from_bottom)

    def capture_slot(self, arm: ArmId, index: int) -> Point:
        """Backward-compatible alias for the side dead-piece line."""
        return self.dead_slot(arm, index)

    def dead_slot_label(self, arm: ArmId, index: int) -> str:
        return dead_label(arm, index)

    def buffer(self, arm: ArmId) -> Point:
        # Staging points 50 mm outside the table's left/right edges, mid-height.
        ox, oy = self.config.table_origin_x_mm, self.config.table_origin_y_mm
        mid_y = oy + self.config.table_height_mm / 2.0
        if arm is ArmId.WHITE:
            return Point(ox - 50.0, mid_y)
        return Point(ox + self.config.table_width_mm + 50.0, mid_y)

    def location(self, name: str) -> Point:
        parts = name.split(":")
        if parts[0] == "board":
            return self.square(parts[1])
        if parts[0] == "dead":
            return self.dead_slot(ArmId(parts[1]), int(parts[2]))
        if parts[0] == "capture":
            return self.capture_slot(ArmId(parts[1]), int(parts[2]))
        if parts[0] == "buffer":
            return self.buffer(ArmId(parts[1]))
        if parts[0] == "park":
            return self.park(ArmId(parts[1]))
        raise ValueError(f"unknown physical location: {name}")

    def all_required_locations(self, arm: ArmId) -> dict[str, Point]:
        result = {f"board:{chess.square_name(s)}": self.square(chess.square_name(s)) for s in chess.SQUARES}
        result.update({f"dead:{arm.value}:{i}": self.dead_slot(arm, i) for i in range(DEAD_SLOTS_PER_ARM)})
        result[f"buffer:{arm.value}"] = self.buffer(arm)
        result[f"park:{arm.value}"] = self.park(arm)
        return result


class ScaraKinematics:
    """Planar 3R inverse kinematics for the dual-arm chess robot.

    Positioning only (no tool orientation constraint). Samples the absolute
    orientation of the distal link, reduces to classic 2R IK on links 1–2,
    then recovers the wrist angle.
    """

    # Degrees between absolute-orientation samples for the distal link.
    _PHI_STEP_DEG = 4.0

    def __init__(self, arm_config: ArmConfig):
        self.config = arm_config

    @staticmethod
    def _within(value: float, limits: tuple[float, float]) -> bool:
        return limits[0] <= value <= limits[1]

    @staticmethod
    def _equivalent_angles(angle_deg: float, limits: tuple[float, float]) -> list[float]:
        """Return motor-space copies of an angle that fit one travel window."""
        low, high = limits
        first = math.ceil((low - angle_deg) / 360.0)
        last = math.floor((high - angle_deg) / 360.0)
        return [angle_deg + 360.0 * turns for turns in range(first, last + 1)]

    @staticmethod
    def _singularity_distance_deg(joint_deg: float) -> float:
        """Distance from straight (0) or folded (+/-180) planar singularities."""
        wrapped = (joint_deg + 180.0) % 360.0 - 180.0
        return min(abs(wrapped), abs(abs(wrapped) - 180.0))

    def _joint_headroom_deg(self, pose: JointPose) -> float:
        shoulder_low, shoulder_high = self.config.shoulder_limits_deg
        elbow_low, elbow_high = self.config.elbow_limits_deg
        wrist_low, wrist_high = self.config.wrist_limits_deg
        return min(
            pose.shoulder_deg - shoulder_low,
            shoulder_high - pose.shoulder_deg,
            pose.elbow_deg - elbow_low,
            elbow_high - pose.elbow_deg,
            pose.wrist_deg - wrist_low,
            wrist_high - pose.wrist_deg,
        )

    def forward(self, pose: JointPose) -> tuple[Point, Point, Point, Point]:
        """Return base, elbow joint, wrist joint, and tool in world mm."""
        cfg = self.config
        base = Point(cfg.base_x_mm, cfg.base_y_mm)
        shoulder = math.radians(pose.shoulder_deg)
        elbow = math.radians(pose.elbow_deg)
        wrist = math.radians(pose.wrist_deg)
        local_elbow = Point(cfg.link_1_mm * math.cos(shoulder), cfg.link_1_mm * math.sin(shoulder))
        local_mid = Point(
            local_elbow.x_mm + cfg.link_2_mm * math.cos(shoulder + elbow),
            local_elbow.y_mm + cfg.link_2_mm * math.sin(shoulder + elbow),
        )
        local_tool = Point(
            local_mid.x_mm + cfg.link_3_mm * math.cos(shoulder + elbow + wrist),
            local_mid.y_mm + cfg.link_3_mm * math.sin(shoulder + elbow + wrist),
        )
        orientation = math.radians(cfg.forward_angle_deg)

        def rotate(local: Point) -> Point:
            return Point(
                base.x_mm + math.cos(orientation) * local.x_mm - math.sin(orientation) * local.y_mm,
                base.y_mm + math.sin(orientation) * local.x_mm + math.cos(orientation) * local.y_mm,
            )

        return base, rotate(local_elbow), rotate(local_mid), rotate(local_tool)

    def inverse(self, point: Point, preferred: JointPose | None = None) -> Reachability:
        dx = point.x_mm - self.config.base_x_mm
        dy = point.y_mm - self.config.base_y_mm
        orientation = math.radians(self.config.forward_angle_deg)
        x = math.cos(-orientation) * dx - math.sin(-orientation) * dy
        y = math.sin(-orientation) * dx + math.cos(-orientation) * dy
        l1, l2, l3 = self.config.link_1_mm, self.config.link_2_mm, self.config.link_3_mm
        radius = math.hypot(x, y)
        max_reach = l1 + l2 + l3
        min_reach = max(0.0, max(l1, l2, l3) - (l1 + l2 + l3 - max(l1, l2, l3)))
        if radius > max_reach + 1e-6 or radius < min_reach - 1e-6:
            return Reachability(False, None, "outside radial workspace")

        candidates: list[tuple[JointPose, float, float]] = []
        phi = -math.pi
        phi_step = math.radians(self._PHI_STEP_DEG)
        while phi <= math.pi + 1e-9:
            wrist_x = x - l3 * math.cos(phi)
            wrist_y = y - l3 * math.sin(phi)
            r2 = wrist_x * wrist_x + wrist_y * wrist_y
            cos_elbow = (r2 - l1 * l1 - l2 * l2) / (2.0 * l1 * l2)
            if -1.000001 <= cos_elbow <= 1.000001:
                cos_elbow = max(-1.0, min(1.0, cos_elbow))
                for elbow_rad in (math.acos(cos_elbow), -math.acos(cos_elbow)):
                    shoulder_rad = math.atan2(wrist_y, wrist_x) - math.atan2(
                        l2 * math.sin(elbow_rad), l1 + l2 * math.cos(elbow_rad)
                    )
                    wrist_rad = phi - (shoulder_rad + elbow_rad)
                    shoulder_deg = math.degrees(shoulder_rad)
                    elbow_deg = math.degrees(elbow_rad)
                    wrist_deg = math.degrees(wrist_rad)
                    for motor_shoulder in self._equivalent_angles(
                        shoulder_deg, self.config.shoulder_limits_deg
                    ):
                        for motor_elbow in self._equivalent_angles(
                            elbow_deg, self.config.elbow_limits_deg
                        ):
                            for motor_wrist in self._equivalent_angles(
                                wrist_deg, self.config.wrist_limits_deg
                            ):
                                pose = JointPose(motor_shoulder, motor_elbow, motor_wrist)
                                headroom = self._joint_headroom_deg(pose)
                                singularity_distance = min(
                                    self._singularity_distance_deg(motor_elbow),
                                    self._singularity_distance_deg(motor_wrist),
                                )
                                if headroom < self.config.joint_limit_margin_deg:
                                    continue
                                if singularity_distance < self.config.singularity_margin_deg:
                                    continue
                                distance = 0.0
                                if preferred:
                                    distance = pose.joint_distance(preferred)
                                candidates.append((pose, distance, singularity_distance))
            phi += phi_step

        if not candidates:
            return Reachability(False, None, "joint limits or safety margin")
        pose, _, singularity_distance = min(
            candidates,
            key=lambda item: item[1]
            if preferred
            else -min(self._joint_headroom_deg(item[0]), item[2]),
        )
        return Reachability(
            True,
            pose,
            singularity_margin=math.sin(math.radians(singularity_distance)),
        )


def validate_layout(config: RobotConfig) -> dict[ArmId, dict[str, Reachability]]:
    layout = BoardLayout(config)
    report: dict[ArmId, dict[str, Reachability]] = {}
    for arm in ArmId:
        solver = ScaraKinematics(config.arm(arm))
        report[arm] = {}
        for name, point in layout.all_required_locations(arm).items():
            if name == f"park:{arm.value}":
                cfg = config.arm(arm)
                report[arm][name] = Reachability(
                    True,
                    JointPose(cfg.home_shoulder_deg, cfg.home_elbow_deg, cfg.home_wrist_deg),
                    "stowed home",
                    1.0,
                )
            else:
                report[arm][name] = solver.inverse(point)
    return report


def unreachable(report: dict[ArmId, dict[str, Reachability]]) -> Iterable[tuple[ArmId, str, Reachability]]:
    for arm, locations in report.items():
        for name, result in locations.items():
            if not result.reachable:
                yield arm, name, result
