"""Offline search and certification for the mirrored dual 3R arm geometry."""

from __future__ import annotations

import dataclasses
import json
import math
from dataclasses import dataclass, asdict
from pathlib import Path

from .config import ArmConfig, ArmId, RobotConfig
from .geometry import BoardLayout, Point, ScaraKinematics  # BoardLayout used in route centers


# MG995-class servos: one continuous 180° travel window per joint.
JOINT_WINDOW_DEG = 180.0


@dataclass(frozen=True)
class GeometryDesign:
    link_1_mm: float
    link_2_mm: float
    link_3_mm: float
    base_x_mm: float
    base_setback_mm: float
    forward_angle_deg: float
    shoulder_window_start_deg: float
    elbow_window_start_deg: float
    wrist_window_start_deg: float

    def config(self) -> RobotConfig:
        """Build one 180-degree mirrored physical configuration."""
        shoulder_limits = (
            self.shoulder_window_start_deg,
            self.shoulder_window_start_deg + JOINT_WINDOW_DEG,
        )
        elbow_limits = (
            self.elbow_window_start_deg,
            self.elbow_window_start_deg + JOINT_WINDOW_DEG,
        )
        wrist_limits = (
            self.wrist_window_start_deg,
            self.wrist_window_start_deg + JOINT_WINDOW_DEG,
        )
        white = ArmConfig(
            base_x_mm=-self.base_x_mm,
            base_y_mm=-self.base_setback_mm,
            forward_angle_deg=self.forward_angle_deg,
            link_1_mm=self.link_1_mm,
            link_2_mm=self.link_2_mm,
            link_3_mm=self.link_3_mm,
            shoulder_limits_deg=shoulder_limits,
            elbow_limits_deg=elbow_limits,
            wrist_limits_deg=wrist_limits,
            park_x_mm=-self.base_x_mm,
            park_y_mm=-self.base_setback_mm,
        )
        black = ArmConfig(
            base_x_mm=self.base_x_mm,
            base_y_mm=self.base_setback_mm,
            forward_angle_deg=self.forward_angle_deg + 180.0,
            link_1_mm=self.link_1_mm,
            link_2_mm=self.link_2_mm,
            link_3_mm=self.link_3_mm,
            shoulder_limits_deg=shoulder_limits,
            elbow_limits_deg=elbow_limits,
            wrist_limits_deg=wrist_limits,
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
    """All horizontal, vertical, and diagonal neighboring piece-cell paths."""
    layout = BoardLayout(config)
    centers = {
        (col, row): layout.cell_center(col, row)
        for col in range(config.table_columns)
        for row in range(config.table_rows)
    }
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
        Point(
            start.x_mm + (end.x_mm - start.x_mm) * index / count,
            start.y_mm + (end.y_mm - start.y_mm) * index / count,
        )
        for index in range(count + 1)
    ]


def evaluate_design(
    design: GeometryDesign, *, grid_mm: int, route_step_mm: int | None = None
) -> GeometryEvaluation:
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
                return GeometryEvaluation(
                    False, checked_points, checked_routes, 1, f"{arm.value}:{name}", 0.0, 0.0
                )
            min_headroom = min(min_headroom, solver._joint_headroom_deg(result.pose))
            min_singularity = min(
                min_singularity,
                solver._singularity_distance_deg(result.pose.elbow_deg),
                solver._singularity_distance_deg(result.pose.wrist_deg),
            )

        for start, end in _route_segments(config):
            preferred = None
            for point in _sample_segment(start, end, route_step_mm):
                result = solver.inverse(point, preferred)
                if not result.reachable or result.pose is None:
                    label = (
                        f"route:{start.x_mm:.0f},{start.y_mm:.0f}"
                        f"->{end.x_mm:.0f},{end.y_mm:.0f}"
                    )
                    return GeometryEvaluation(
                        False,
                        checked_points,
                        checked_routes,
                        1,
                        f"{arm.value}:{label}",
                        0.0,
                        0.0,
                    )
                preferred = result.pose
                min_headroom = min(min_headroom, solver._joint_headroom_deg(result.pose))
                min_singularity = min(
                    min_singularity,
                    solver._singularity_distance_deg(result.pose.elbow_deg),
                    solver._singularity_distance_deg(result.pose.wrist_deg),
                )
            checked_routes += 1

    return GeometryEvaluation(
        True, checked_points, checked_routes, 0, "", min_headroom, min_singularity
    )


def _shape_precheck(link_1: float, link_2: float, link_3: float, base_x: float, setback: float) -> bool:
    """Cheap radial filter before running IK/window certification."""
    max_radius = link_1 + link_2 + link_3 - 5.0
    min_radius = 20.0
    base = Point(-base_x, -setback)
    # Envelope of outermost piece-cell centers on the default table (~640 mm wide).
    half_w = 295.0  # ± (640/2 - 25)
    half_h = 175.0  # ± (400/2 - 25)
    corners = [Point(x, y) for x in (-half_w, half_w) for y in (-half_h, half_h)]
    if max(math.hypot(point.x_mm - base.x_mm, point.y_mm - base.y_mm) for point in corners) > max_radius:
        return False
    nearest_x = min(max(base.x_mm, -half_w), half_w)
    nearest_y = min(max(base.y_mm, -half_h), half_h)
    if math.hypot(nearest_x - base.x_mm, nearest_y - base.y_mm) < min_radius:
        return False
    # White-side buffer sits 50 mm outside the left table edge.
    for point in (Point(-400.0, 0.0),):
        radius = math.hypot(point.x_mm - base.x_mm, point.y_mm - base.y_mm)
        if radius > max_radius:
            return False
    return True


def _shape_candidates() -> list[tuple[float, float, float, float, float, float]]:
    """Coarse deterministic search space, sorted by total length then max link.

    Link lengths are free to differ (MG995 3R redesign). Prefer shorter totals.
    """
    shapes: list[tuple[float, float, float, float, float, float]] = []
    for link_1 in range(160, 241, 10):
        for link_2 in range(140, 221, 10):
            for link_3 in range(140, 221, 10):
                total = link_1 + link_2 + link_3
                if total < 520 or total > 600:
                    continue
                for base_x in (0,):
                    for setback in (255,):
                        for heading in (45, 60, 75, 90):
                            if _shape_precheck(link_1, link_2, link_3, base_x, setback):
                                shapes.append(
                                    (
                                        float(link_1),
                                        float(link_2),
                                        float(link_3),
                                        float(base_x),
                                        float(setback),
                                        float(heading),
                                    )
                                )
    return sorted(
        shapes,
        key=lambda item: (item[0] + item[1] + item[2], max(item[0], item[1], item[2]), item[0]),
    )


def _window_starts_for_shape(
    link_1: float,
    link_2: float,
    link_3: float,
    base_x: float,
    setback: float,
    heading: float,
    grid_mm: int,
) -> tuple[list[float], list[float], list[float]]:
    """Derive plausible motor-zero windows from permissive coarse IK poses."""
    raw = GeometryDesign(link_1, link_2, link_3, base_x, setback, heading, -180.0, -180.0, -180.0).config()
    raw = dataclasses.replace(
        raw,
        white_arm=dataclasses.replace(
            raw.white_arm,
            shoulder_limits_deg=(-720.0, 720.0),
            elbow_limits_deg=(-720.0, 720.0),
            wrist_limits_deg=(-720.0, 720.0),
            joint_limit_margin_deg=0.0,
            singularity_margin_deg=0.0,
        ),
        black_arm=dataclasses.replace(
            raw.black_arm,
            shoulder_limits_deg=(-720.0, 720.0),
            elbow_limits_deg=(-720.0, 720.0),
            wrist_limits_deg=(-720.0, 720.0),
            joint_limit_margin_deg=0.0,
            singularity_margin_deg=0.0,
        ),
    )
    solver = ScaraKinematics(raw.white_arm)
    angles: list[tuple[float, float, float]] = []
    for _, point in [*_table_points(raw, grid_mm), *_required_off_table_points(raw, ArmId.WHITE)]:
        result = solver.inverse(point)
        if not result.reachable or result.pose is None:
            return [], [], []
        angles.append((result.pose.shoulder_deg, result.pose.elbow_deg, result.pose.wrist_deg))

    def fitting_starts(values: list[float]) -> list[float]:
        starts: list[float] = []
        # Usable span is window minus 2 * margin (5° each side → 170° usable).
        usable = JOINT_WINDOW_DEG - 10.0
        for start in range(-180, 181, 15):
            low, high = start + 5.0, start + 5.0 + usable
            if all(
                any(low <= value + 360.0 * turns <= high for turns in range(-2, 3))
                for value in values
            ):
                starts.append(float(start))
        return starts

    return (
        fitting_starts([item[0] for item in angles]),
        fitting_starts([item[1] for item in angles]),
        fitting_starts([item[2] for item in angles]),
    )


def optimize_geometry(*, coarse_grid_mm: int = 50, final_grid_mm: int = 5) -> OptimizationResult:
    """Find a short certified mirrored 3R design in the agreed search range."""
    # Prefer shorter mechanical shapes. Coarse certificate first; accepted
    # designs are re-checked at the final grid before return.
    # Seed the known good unequal MG995 candidate early.
    preferred = [
        (200.0, 160.0, 180.0, 0.0, 255.0, 45.0),
        (190.0, 170.0, 180.0, 0.0, 255.0, 45.0),
        (200.0, 170.0, 170.0, 0.0, 255.0, 45.0),
        (200.0, 180.0, 160.0, 0.0, 255.0, 45.0),
        (180.0, 180.0, 180.0, 0.0, 255.0, 45.0),
    ]
    seen: set[tuple[float, float, float, float, float, float]] = set()
    ordered: list[tuple[float, float, float, float, float, float]] = []
    for shape in [*preferred, *_shape_candidates()]:
        if shape not in seen:
            seen.add(shape)
            ordered.append(shape)

    # Fold-friendly windows that leave elbow/wrist able to reach ~180°.
    seed_windows = [
        (-90.0, 0.0, 0.0),
        (-90.0, 0.0, -90.0),
        (0.0, 0.0, 0.0),
        (-90.0, -90.0, 0.0),
    ]

    for link_1, link_2, link_3, base_x, setback, heading in ordered:
        shoulder_starts, elbow_starts, wrist_starts = _window_starts_for_shape(
            link_1, link_2, link_3, base_x, setback, heading, coarse_grid_mm
        )
        shoulder_starts = sorted(set([*shoulder_starts, *(s for s, _, _ in seed_windows)]))
        elbow_starts = sorted(set([*elbow_starts, *(e for _, e, _ in seed_windows)]))
        wrist_starts = sorted(set([*wrist_starts, *(w for _, _, w in seed_windows)]))
        # Try seed triples first, then the cartesian product of fitted starts
        # (capped for tractability).
        trial_windows: list[tuple[float, float, float]] = list(seed_windows)
        for shoulder_start in shoulder_starts[:8]:
            for elbow_start in elbow_starts[:8]:
                for wrist_start in wrist_starts[:8]:
                    trial = (shoulder_start, elbow_start, wrist_start)
                    if trial not in trial_windows:
                        trial_windows.append(trial)
        for shoulder_start, elbow_start, wrist_start in trial_windows:
            design = GeometryDesign(
                link_1,
                link_2,
                link_3,
                base_x,
                setback,
                heading,
                shoulder_start,
                elbow_start,
                wrist_start,
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
