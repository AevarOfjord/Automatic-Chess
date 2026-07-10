"""Offline search and certification for the mirrored dual-SCARA geometry."""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

from .config import ArmConfig, ArmId, RobotConfig
from .geometry import BoardLayout, Point, ScaraKinematics


@dataclass(frozen=True)
class GeometryDesign:
    link_1_mm: float
    link_2_mm: float
    base_x_mm: float
    base_setback_mm: float
    forward_angle_deg: float
    shoulder_window_start_deg: float
    elbow_window_start_deg: float

    def config(self) -> RobotConfig:
        """Build one 180-degree mirrored physical configuration."""
        shoulder_limits = (self.shoulder_window_start_deg, self.shoulder_window_start_deg + 270.0)
        elbow_limits = (self.elbow_window_start_deg, self.elbow_window_start_deg + 270.0)
        white = ArmConfig(
            base_x_mm=-self.base_x_mm,
            base_y_mm=-self.base_setback_mm,
            forward_angle_deg=self.forward_angle_deg,
            link_1_mm=self.link_1_mm,
            link_2_mm=self.link_2_mm,
            shoulder_limits_deg=shoulder_limits,
            elbow_limits_deg=elbow_limits,
            park_x_mm=-self.base_x_mm,
            park_y_mm=-self.base_setback_mm,
        )
        black = ArmConfig(
            base_x_mm=self.base_x_mm,
            base_y_mm=self.base_setback_mm,
            forward_angle_deg=self.forward_angle_deg + 180.0,
            link_1_mm=self.link_1_mm,
            link_2_mm=self.link_2_mm,
            shoulder_limits_deg=shoulder_limits,
            elbow_limits_deg=elbow_limits,
            park_x_mm=self.base_x_mm,
            park_y_mm=self.base_setback_mm,
        )
        return dataclasses.replace(RobotConfig(), white_arm=white, black_arm=black)


@dataclass(frozen=True)
class GeometryEvaluation:
    valid: bool
    checked_points: int
    checked_routes: int
    unreachable_points: int
    worst_location: str
    min_joint_headroom_deg: float
    min_singularity_distance_deg: float


@dataclass(frozen=True)
class OptimizationResult:
    design: GeometryDesign
    evaluation: GeometryEvaluation
    coarse_grid_mm: int
    final_grid_mm: int


def _table_points(config: RobotConfig, step_mm: int) -> list[tuple[str, Point]]:
    points: list[tuple[str, Point]] = []
    # A carried puck occupies cell centers and moves between them. Certify the
    # entire continuous envelope from the outermost center to outermost center,
    # rather than impossible puck-center positions beyond the table cells.
    x0 = config.table_origin_x_mm + config.square_size_mm / 2.0
    y0 = config.table_origin_y_mm + config.square_size_mm / 2.0
    width = int(config.table_width_mm - config.square_size_mm)
    height = int(config.table_height_mm - config.square_size_mm)
    for x in range(0, width + 1, step_mm):
        for y in range(0, height + 1, step_mm):
            points.append((f"table:{x0 + x:.0f},{y0 + y:.0f}", Point(x0 + x, y0 + y)))
    return points


def _required_off_table_points(config: RobotConfig, arm: ArmId) -> list[tuple[str, Point]]:
    layout = BoardLayout(config)
    return [
        (f"buffer:{arm.value}", layout.buffer(arm)),
    ]


def _route_segments(config: RobotConfig) -> list[tuple[Point, Point]]:
    """All horizontal, vertical, and diagonal neighboring cell-center paths."""
    x0, y0, size = config.table_origin_x_mm, config.table_origin_y_mm, config.square_size_mm
    centers = {(col, row): Point(x0 + (col + 0.5) * size, y0 + (row + 0.5) * size)
               for col in range(config.table_columns) for row in range(config.table_rows)}
    segments: list[tuple[Point, Point]] = []
    for col in range(config.table_columns):
        for row in range(config.table_rows):
            for dx, dy in ((1, 0), (0, 1), (1, 1), (1, -1)):
                other = (col + dx, row + dy)
                if other in centers:
                    segments.append((centers[(col, row)], centers[other]))
    return segments


def _sample_segment(start: Point, end: Point, step_mm: int) -> list[Point]:
    length = math.hypot(end.x_mm - start.x_mm, end.y_mm - start.y_mm)
    count = max(1, math.ceil(length / step_mm))
    return [
        Point(start.x_mm + (end.x_mm - start.x_mm) * index / count,
              start.y_mm + (end.y_mm - start.y_mm) * index / count)
        for index in range(count + 1)
    ]


def evaluate_design(design: GeometryDesign, *, grid_mm: int, route_step_mm: int | None = None) -> GeometryEvaluation:
    """Certify point coverage and continuous-grid route coverage for both arms."""
    config = design.config()
    points = _table_points(config, grid_mm)
    route_step_mm = route_step_mm or grid_mm
    checked_points = 0
    checked_routes = 0
    min_headroom = math.inf
    min_singularity = math.inf

    for arm in ArmId:
        solver = ScaraKinematics(config.arm(arm))
        for name, point in [*points, *_required_off_table_points(config, arm)]:
            result = solver.inverse(point)
            checked_points += 1
            if not result.reachable or result.pose is None:
                return GeometryEvaluation(False, checked_points, checked_routes, 1, f"{arm.value}:{name}", 0.0, 0.0)
            min_headroom = min(min_headroom, solver._joint_headroom_deg(result.pose))
            min_singularity = min(min_singularity, solver._singularity_distance_deg(result.pose.elbow_deg))

        for start, end in _route_segments(config):
            preferred = None
            for point in _sample_segment(start, end, route_step_mm):
                result = solver.inverse(point, preferred)
                if not result.reachable or result.pose is None:
                    label = f"route:{start.x_mm:.0f},{start.y_mm:.0f}->{end.x_mm:.0f},{end.y_mm:.0f}"
                    return GeometryEvaluation(False, checked_points, checked_routes, 1, f"{arm.value}:{label}", 0.0, 0.0)
                preferred = result.pose
                min_headroom = min(min_headroom, solver._joint_headroom_deg(result.pose))
                min_singularity = min(min_singularity, solver._singularity_distance_deg(result.pose.elbow_deg))
            checked_routes += 1

    return GeometryEvaluation(True, checked_points, checked_routes, 0, "", min_headroom, min_singularity)


def _shape_precheck(link_1: float, link_2: float, base_x: float, setback: float) -> bool:
    """Cheap annulus filter before running IK/window certification."""
    max_radius = math.sqrt(
        link_1 * link_1 + link_2 * link_2 + 2.0 * link_1 * link_2 * math.cos(math.radians(15.0))
    )
    min_radius = math.sqrt(
        link_1 * link_1 + link_2 * link_2 - 2.0 * link_1 * link_2 * math.cos(math.radians(15.0))
    )
    base = Point(-base_x, -setback)
    # Farthest and nearest points of the carried-puck operating envelope.
    corners = [Point(x, y) for x in (-275.0, 275.0) for y in (-175.0, 175.0)]
    if max(math.hypot(point.x_mm - base.x_mm, point.y_mm - base.y_mm) for point in corners) > max_radius:
        return False
    nearest_x = min(max(base.x_mm, -275.0), 275.0)
    nearest_y = min(max(base.y_mm, -175.0), 175.0)
    if math.hypot(nearest_x - base.x_mm, nearest_y - base.y_mm) < min_radius:
        return False
    for point in (Point(-350.0, 0.0),):
        radius = math.hypot(point.x_mm - base.x_mm, point.y_mm - base.y_mm)
        if not min_radius <= radius <= max_radius:
            return False
    return True


def _shape_candidates() -> list[tuple[float, float, float, float, float]]:
    """Coarse deterministic search space, sorted by the requested objective."""
    shapes: list[tuple[float, float, float, float, float]] = []
    for link_1 in range(150, 451, 10):
        for link_2 in (link_1,):
            for base_x in range(0, 301, 50):
                for setback in (250,):
                    for heading in range(60, 121, 15):
                        if _shape_precheck(link_1, link_2, base_x, setback):
                            shapes.append((float(link_1), float(link_2), float(base_x), float(setback), float(heading)))
    return sorted(shapes, key=lambda item: (max(item[0], item[1]), item[0] + item[1], item[3], item[2]))


def _window_starts_for_shape(
    link_1: float,
    link_2: float,
    base_x: float,
    setback: float,
    heading: float,
    grid_mm: int,
) -> tuple[list[float], list[float]]:
    """Derive plausible motor-zero windows from permissive coarse IK poses."""
    raw = GeometryDesign(link_1, link_2, base_x, setback, heading, -180.0, -180.0).config()
    raw = dataclasses.replace(
        raw,
        white_arm=dataclasses.replace(
            raw.white_arm,
            shoulder_limits_deg=(-720.0, 720.0),
            elbow_limits_deg=(-720.0, 720.0),
            joint_limit_margin_deg=0.0,
        ),
        black_arm=dataclasses.replace(
            raw.black_arm,
            shoulder_limits_deg=(-720.0, 720.0),
            elbow_limits_deg=(-720.0, 720.0),
            joint_limit_margin_deg=0.0,
        ),
    )
    solver = ScaraKinematics(raw.white_arm)
    angles: list[tuple[float, float]] = []
    for _, point in [*_table_points(raw, grid_mm), *_required_off_table_points(raw, ArmId.WHITE)]:
        result = solver.inverse(point)
        if not result.reachable or result.pose is None:
            return [], []
        angles.append((result.pose.shoulder_deg, result.pose.elbow_deg))

    def fitting_starts(values: list[float]) -> list[float]:
        starts: list[float] = []
        for start in range(-360, 361, 15):
            low, high = start + 15.0, start + 255.0
            if all(any(low <= value + 360.0 * turns <= high for turns in range(-2, 3)) for value in values):
                starts.append(float(start))
        return starts

    return fitting_starts([item[0] for item in angles]), fitting_starts([item[1] for item in angles])


def optimize_geometry(*, coarse_grid_mm: int = 50, final_grid_mm: int = 1) -> OptimizationResult:
    """Find the shortest certified mirrored design in the agreed search range."""
    # Search the shortest mechanical shapes first. The coarse certificate keeps
    # the window search tractable; every accepted design is then certified at
    # 1 mm before it can be returned.
    for link_1, link_2, base_x, setback, heading in _shape_candidates():
        shoulder_starts, elbow_starts = _window_starts_for_shape(
            link_1, link_2, base_x, setback, heading, coarse_grid_mm
        )
        # Keep the calibrated folded-home windows in the search even when the
        # permissive IK branch chooser reports an equivalent +360-degree form.
        shoulder_starts = sorted(set([*shoulder_starts, -135.0]))
        elbow_starts = sorted(set([*elbow_starts, -345.0]))
        for shoulder_start in shoulder_starts:
            for elbow_start in elbow_starts:
                design = GeometryDesign(
                    link_1,
                    link_2,
                    base_x,
                    setback,
                    heading,
                    shoulder_start,
                    elbow_start,
                )
                coarse = evaluate_design(design, grid_mm=coarse_grid_mm)
                if not coarse.valid:
                    continue
                final = evaluate_design(design, grid_mm=final_grid_mm)
                if final.valid:
                    return OptimizationResult(design, final, coarse_grid_mm, final_grid_mm)
    raise RuntimeError("no geometry satisfies the requested coverage and safety constraints")


def write_report(result: OptimizationResult, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(result), indent=2, sort_keys=True) + "\n", encoding="utf-8")
