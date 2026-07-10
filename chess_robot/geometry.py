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
    shoulder_deg: float
    elbow_deg: float

    def as_wire(self, z_mm: float, speed: int = 2400, acceleration: int = 1200) -> list[float | int]:
        return [
            round(self.shoulder_deg, 3),
            round(self.elbow_deg, 3),
            round(z_mm, 2),
            speed,
            acceleration,
        ]


@dataclass(frozen=True)
class Reachability:
    reachable: bool
    pose: JointPose | None
    reason: str = ""
    singularity_margin: float = 0.0


class BoardLayout:
    """Maps named chess/inventory locations into the shared millimetre frame.

    Grid naming (all 0-based indices converted to 1-based labels):

    - **Table columns** ``C1…C12`` left → right (+X).
    - **Table rows** ``R1…R8`` bottom → top (+Y), same sense as chess ranks.
    - **Chess play area** occupies ``C3…C10`` = files ``a…h``, ranks ``1…8``.
    - **White dead rack** ``C1–C2`` labeled ``W1…W16`` (fills top → bottom).
    - **Black dead rack** ``C11–C12`` labeled ``B1…B16`` (fills top → bottom).
    """

    def __init__(self, config: RobotConfig):
        self.config = config

    @property
    def chess_start_col(self) -> int:
        """0-based table column of chess file a."""
        return round(self.config.board_origin_x_mm / self.config.square_size_mm)

    @property
    def chess_end_col(self) -> int:
        """0-based exclusive end column of the chess area."""
        return self.chess_start_col + self.config.board_squares

    def column_label(self, table_col: int) -> str:
        """Human label for a 0-based table column (``C1``…``C12``)."""
        if not 0 <= table_col < self.config.table_columns:
            raise ValueError(f"table column out of range: {table_col}")
        return f"C{table_col + 1}"

    def row_label(self, row_from_bottom: int) -> str:
        """Human label for a 0-based row from the bottom (``R1``…``R8``)."""
        if not 0 <= row_from_bottom < self.config.table_rows:
            raise ValueError(f"table row out of range: {row_from_bottom}")
        return f"R{row_from_bottom + 1}"

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
        # White rack: C1–C2 (columns 0–1), Black rack: C11–C12.
        if 0 <= table_col <= 1:
            arm = ArmId.WHITE
            col_in_rack = table_col
        elif self.config.table_columns - 2 <= table_col < self.config.table_columns:
            arm = ArmId.BLACK
            col_in_rack = table_col - (self.config.table_columns - 2)
        else:
            return None
        index = row_from_top * 2 + col_in_rack
        if not 0 <= index < DEAD_SLOTS_PER_ARM:
            return None
        return arm, index

    def cell_label(self, table_col: int, row_from_bottom: int) -> str:
        """Primary display name for a table cell (chess square or W/B rack id)."""
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
        row_from_top, col_in_rack = divmod(index, 2)
        table_col = col_in_rack if arm is ArmId.WHITE else self.config.table_columns - 2 + col_in_rack
        row_from_bottom = self.config.table_rows - 1 - row_from_top
        x = self.config.table_origin_x_mm + (table_col + 0.5) * self.config.square_size_mm
        y = self.config.table_origin_y_mm + (row_from_bottom + 0.5) * self.config.square_size_mm
        return Point(x, y)

    def capture_slot(self, arm: ArmId, index: int) -> Point:
        """Backward-compatible alias for the side dead-piece line."""
        return self.dead_slot(arm, index)

    def dead_slot_label(self, arm: ArmId, index: int) -> str:
        return dead_label(arm, index)

    def buffer(self, arm: ArmId) -> Point:
        # Table-relative staging points just off the board's left/right edge.
        ox, oy = self.config.table_origin_x_mm, self.config.table_origin_y_mm
        return Point(ox - 50.0, oy + 200.0) if arm is ArmId.WHITE else Point(ox + 450.0, oy + 200.0)

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
    def _singularity_distance_deg(elbow_deg: float) -> float:
        """Distance from straight (0) or folded (+/-180) SCARA singularities."""
        wrapped = (elbow_deg + 180.0) % 360.0 - 180.0
        return min(abs(wrapped), abs(abs(wrapped) - 180.0))

    def _joint_headroom_deg(self, pose: JointPose) -> float:
        shoulder_low, shoulder_high = self.config.shoulder_limits_deg
        elbow_low, elbow_high = self.config.elbow_limits_deg
        return min(
            pose.shoulder_deg - shoulder_low,
            shoulder_high - pose.shoulder_deg,
            pose.elbow_deg - elbow_low,
            elbow_high - pose.elbow_deg,
        )

    def inverse(self, point: Point, preferred: JointPose | None = None) -> Reachability:
        dx = point.x_mm - self.config.base_x_mm
        dy = point.y_mm - self.config.base_y_mm
        orientation = math.radians(self.config.forward_angle_deg)
        x = math.cos(-orientation) * dx - math.sin(-orientation) * dy
        y = math.sin(-orientation) * dx + math.cos(-orientation) * dy
        l1, l2 = self.config.link_1_mm, self.config.link_2_mm
        cos_elbow = (x * x + y * y - l1 * l1 - l2 * l2) / (2.0 * l1 * l2)
        if cos_elbow < -1.000001 or cos_elbow > 1.000001:
            return Reachability(False, None, "outside radial workspace")
        cos_elbow = max(-1.0, min(1.0, cos_elbow))
        candidates: list[tuple[JointPose, float, float]] = []
        for elbow_rad in (math.acos(cos_elbow), -math.acos(cos_elbow)):
            shoulder_rad = math.atan2(y, x) - math.atan2(
                l2 * math.sin(elbow_rad), l1 + l2 * math.cos(elbow_rad)
            )
            shoulder_deg = math.degrees(shoulder_rad)
            elbow_deg = math.degrees(elbow_rad)
            for motor_shoulder in self._equivalent_angles(shoulder_deg, self.config.shoulder_limits_deg):
                for motor_elbow in self._equivalent_angles(elbow_deg, self.config.elbow_limits_deg):
                    pose = JointPose(motor_shoulder, motor_elbow)
                    headroom = self._joint_headroom_deg(pose)
                    singularity_distance = self._singularity_distance_deg(motor_elbow)
                    if headroom < self.config.joint_limit_margin_deg:
                        continue
                    if singularity_distance < self.config.singularity_margin_deg:
                        continue
                    distance = 0.0
                    if preferred:
                        distance = abs(pose.shoulder_deg - preferred.shoulder_deg) + abs(
                            pose.elbow_deg - preferred.elbow_deg
                        )
                    candidates.append((pose, distance, singularity_distance))
        if not candidates:
            return Reachability(False, None, "joint limits or safety margin")
        pose, _, singularity_distance = min(
            candidates,
            key=lambda item: item[1] if preferred else -min(self._joint_headroom_deg(item[0]), item[2]),
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
                    JointPose(cfg.home_shoulder_deg, cfg.home_elbow_deg),
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
