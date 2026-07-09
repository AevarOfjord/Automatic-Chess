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
    """Maps named chess/inventory locations into the shared millimetre frame."""

    def __init__(self, config: RobotConfig):
        self.config = config

    def square(self, square_name: str) -> Point:
        square = chess.parse_square(square_name)
        return Point(
            self.config.board_origin_x_mm
            + (chess.square_file(square) + 0.5) * self.config.square_size_mm,
            self.config.board_origin_y_mm
            + (chess.square_rank(square) + 0.5) * self.config.square_size_mm,
        )

    def park(self, arm: ArmId) -> Point:
        cfg = self.config.arm(arm)
        return Point(cfg.park_x_mm, cfg.park_y_mm)

    def dead_slot(self, arm: ArmId, index: int) -> Point:
        if not 0 <= index < DEAD_SLOTS_PER_ARM:
            raise ValueError(f"dead-piece slot out of range: {index}")
        row, col = divmod(index, 2)
        table_col = col if arm is ArmId.WHITE else self.config.table_columns - 2 + col
        table_row_from_top = row
        x = (table_col + 0.5) * self.config.square_size_mm
        y = (self.config.table_rows - table_row_from_top - 0.5) * self.config.square_size_mm
        return Point(x, y)

    def capture_slot(self, arm: ArmId, index: int) -> Point:
        """Backward-compatible alias for the side dead-piece line."""
        return self.dead_slot(arm, index)

    def dead_slot_label(self, arm: ArmId, index: int) -> str:
        return dead_label(arm, index)

    def buffer(self, arm: ArmId) -> Point:
        return Point(-50.0, 200.0) if arm is ArmId.WHITE else Point(450.0, 200.0)

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
        candidates: list[tuple[JointPose, float]] = []
        for elbow_rad in (math.acos(cos_elbow), -math.acos(cos_elbow)):
            shoulder_rad = math.atan2(y, x) - math.atan2(
                l2 * math.sin(elbow_rad), l1 + l2 * math.cos(elbow_rad)
            )
            pose = JointPose(math.degrees(shoulder_rad), math.degrees(elbow_rad))
            margin = abs(math.sin(elbow_rad))
            if self._within(pose.shoulder_deg, self.config.shoulder_limits_deg) and self._within(
                pose.elbow_deg, self.config.elbow_limits_deg
            ):
                distance = 0.0
                if preferred:
                    distance = abs(pose.shoulder_deg - preferred.shoulder_deg) + abs(
                        pose.elbow_deg - preferred.elbow_deg
                    )
                candidates.append((pose, distance))
        if not candidates:
            return Reachability(False, None, "joint limits")
        candidates.sort(key=lambda item: (-item[0].elbow_deg, item[1]))
        pose = min(candidates, key=lambda item: item[1] if preferred else -item[0].elbow_deg)[0]
        margin = abs(math.sin(math.radians(pose.elbow_deg)))
        if margin < 0.08:
            return Reachability(False, pose, "too close to a singular pose", margin)
        return Reachability(True, pose, singularity_margin=margin)


def validate_layout(config: RobotConfig) -> dict[ArmId, dict[str, Reachability]]:
    layout = BoardLayout(config)
    report: dict[ArmId, dict[str, Reachability]] = {}
    for arm in ArmId:
        solver = ScaraKinematics(config.arm(arm))
        report[arm] = {
            name: solver.inverse(point) for name, point in layout.all_required_locations(arm).items()
        }
    return report


def unreachable(report: dict[ArmId, dict[str, Reachability]]) -> Iterable[tuple[ArmId, str, Reachability]]:
    for arm, locations in report.items():
        for name, result in locations.items():
            if not result.reachable:
                yield arm, name, result
