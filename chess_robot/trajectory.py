from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .config import ArmId, RobotConfig
from .geometry import BoardLayout, Point
from .inventory import PhysicalInventory


class TrajectoryPlanningError(RuntimeError):
    pass


@dataclass(frozen=True)
class PuckModel:
    puck_diameter_mm: float = 30.0
    clearance_mm: float = 2.0

    @property
    def puck_radius_mm(self) -> float:
        return self.puck_diameter_mm / 2.0

    @property
    def center_clearance_mm(self) -> float:
        return self.puck_diameter_mm + self.clearance_mm


@dataclass(frozen=True)
class Obstacle:
    token_id: str
    location: str
    center: Point


@dataclass(frozen=True)
class PlannedPath:
    points: list[Point]
    obstacles: list[Obstacle]

    @property
    def travel_mm(self) -> float:
        return sum(
            math.hypot(
                self.points[index + 1].x_mm - self.points[index].x_mm,
                self.points[index + 1].y_mm - self.points[index].y_mm,
            )
            for index in range(len(self.points) - 1)
        )


class PuckTrajectoryPlanner:
    """Plans fixed-height XY puck motion around other pucks."""

    def __init__(
        self,
        config: RobotConfig | None = None,
        layout: BoardLayout | None = None,
        puck: PuckModel | None = None,
    ) -> None:
        self.config = config or RobotConfig()
        self.layout = layout or BoardLayout(self.config)
        self.puck = puck or PuckModel()

    def plan_transfer(
        self,
        inventory: PhysicalInventory,
        moving_token_id: str,
        source: str,
        destination: str,
    ) -> PlannedPath:
        start = self.layout.location(source)
        end = self.layout.location(destination)
        obstacles = self.obstacles_from_inventory(inventory, moving_token_id)
        points = self.plan_points(start, end, obstacles)
        return PlannedPath(points, obstacles)

    def obstacles_from_inventory(
        self, inventory: PhysicalInventory, moving_token_id: str | None = None
    ) -> list[Obstacle]:
        obstacles: list[Obstacle] = []
        for token_id, location in inventory.locations.items():
            if token_id == moving_token_id:
                continue
            try:
                center = self.layout.location(location)
            except ValueError:
                continue
            obstacles.append(Obstacle(token_id, location, center))
        return obstacles

    def plan_points(self, start: Point, end: Point, obstacles: list[Obstacle]) -> list[Point]:
        if self.segment_clear(start, end, obstacles):
            return [start, end]

        candidates = self._candidate_points(start, end, obstacles)
        start_index = 0
        end_index = 1
        distances = [math.inf] * len(candidates)
        previous: list[int | None] = [None] * len(candidates)
        distances[start_index] = 0.0
        queue: list[tuple[float, int]] = [(0.0, start_index)]

        while queue:
            distance, current = heapq.heappop(queue)
            if distance > distances[current]:
                continue
            if current == end_index:
                break
            for neighbor, weight in self._neighbors(current, candidates, obstacles):
                candidate_distance = distance + weight
                if candidate_distance < distances[neighbor]:
                    distances[neighbor] = candidate_distance
                    previous[neighbor] = current
                    heapq.heappush(queue, (candidate_distance, neighbor))

        if distances[end_index] == math.inf:
            raise TrajectoryPlanningError("no collision-free puck path found")

        path_indices: list[int] = []
        cursor: int | None = end_index
        while cursor is not None:
            path_indices.append(cursor)
            cursor = previous[cursor]
        path_indices.reverse()
        return [candidates[index] for index in path_indices]

    def segment_clear(self, a: Point, b: Point, obstacles: list[Obstacle]) -> bool:
        # Bounding-box prefilter: the R-neighborhood of a segment is contained
        # in the segment AABB expanded by R.
        clearance = self.puck.center_clearance_mm - 1e-6
        min_x = min(a.x_mm, b.x_mm) - clearance
        max_x = max(a.x_mm, b.x_mm) + clearance
        min_y = min(a.y_mm, b.y_mm) - clearance
        max_y = max(a.y_mm, b.y_mm) + clearance
        for obstacle in obstacles:
            cx, cy = obstacle.center.x_mm, obstacle.center.y_mm
            if cx < min_x or cx > max_x or cy < min_y or cy > max_y:
                continue
            if self._distance_point_to_segment(obstacle.center, a, b) < clearance:
                return False
        return True

    def point_clear(self, point: Point, obstacles: list[Obstacle]) -> bool:
        clearance = self.puck.center_clearance_mm - 1e-6
        for obstacle in obstacles:
            if (
                math.hypot(point.x_mm - obstacle.center.x_mm, point.y_mm - obstacle.center.y_mm)
                < clearance
            ):
                return False
        return True

    def _candidate_points(self, start: Point, end: Point, obstacles: list[Obstacle]) -> list[Point]:
        points = [start, end]
        step = self.config.square_size_mm
        ox, oy = self.config.table_origin_x_mm, self.config.table_origin_y_mm
        min_x = ox + self.puck.puck_radius_mm
        max_x = ox + self.config.table_width_mm - self.puck.puck_radius_mm
        min_y = oy + self.puck.puck_radius_mm
        max_y = oy + self.config.table_height_mm - self.puck.puck_radius_mm

        # Piece-cell centers (accounts for thin separators between racks and board).
        for col in range(self.config.table_columns):
            for row in range(self.config.table_rows):
                point = self.layout.cell_center(col, row)
                if self.point_clear(point, obstacles):
                    points.append(point)

        # Corners of piece cells (shared edges within a region).
        for col in range(self.config.table_columns):
            left = self.config.column_left_x_mm(col)
            for row in range(self.config.table_rows):
                bottom = oy + row * step
                for x, y in (
                    (left, bottom),
                    (left + step, bottom),
                    (left, bottom + step),
                    (left + step, bottom + step),
                ):
                    point = Point(x, y)
                    if min_x <= point.x_mm <= max_x and min_y <= point.y_mm <= max_y:
                        if self.point_clear(point, obstacles):
                            points.append(point)

        # Midlines of the empty separator lanes (corridor waypoints).
        for left_sep in (True, False):
            x = self.layout.separator_center_x(left=left_sep)
            for row in range(self.config.table_rows):
                point = Point(x, oy + (row + 0.5) * step)
                if self.point_clear(point, obstacles):
                    points.append(point)

        for col in range(self.config.table_columns):
            x = self.layout.cell_center(col, 0).x_mm
            for y in (min_y, max_y):
                point = Point(x, y)
                if self.point_clear(point, obstacles):
                    points.append(point)
        for row in range(self.config.table_rows):
            y = oy + (row + 0.5) * step
            for x in (min_x, max_x):
                point = Point(x, y)
                if self.point_clear(point, obstacles):
                    points.append(point)

        for arm in ArmId:
            for point in (self.layout.park(arm), self.layout.buffer(arm)):
                if self.point_clear(point, obstacles):
                    points.append(point)

        return self._dedupe(points)

    def _neighbors(
        self, current: int, points: list[Point], obstacles: list[Obstacle]
    ) -> list[tuple[int, float]]:
        result: list[tuple[int, float]] = []
        a = points[current]
        for index, b in enumerate(points):
            if index == current:
                continue
            if not self.segment_clear(a, b, obstacles):
                continue
            distance = math.hypot(b.x_mm - a.x_mm, b.y_mm - a.y_mm)
            result.append((index, distance))
        return result

    @staticmethod
    def _distance_point_to_segment(point: Point, a: Point, b: Point) -> float:
        dx = b.x_mm - a.x_mm
        dy = b.y_mm - a.y_mm
        length_squared = dx * dx + dy * dy
        if length_squared == 0:
            return math.hypot(point.x_mm - a.x_mm, point.y_mm - a.y_mm)
        t = ((point.x_mm - a.x_mm) * dx + (point.y_mm - a.y_mm) * dy) / length_squared
        t = max(0.0, min(1.0, t))
        projection = Point(a.x_mm + t * dx, a.y_mm + t * dy)
        return math.hypot(point.x_mm - projection.x_mm, point.y_mm - projection.y_mm)

    @staticmethod
    def _dedupe(points: list[Point]) -> list[Point]:
        seen: set[tuple[float, float]] = set()
        result: list[Point] = []
        for point in points:
            key = (round(point.x_mm, 6), round(point.y_mm, 6))
            if key in seen:
                continue
            seen.add(key)
            result.append(point)
        return result
