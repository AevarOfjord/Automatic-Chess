from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

from .config import RobotConfig
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

        if previous[end_index] is None:
            raise TrajectoryPlanningError("no collision-free puck path found")

        path_indices: list[int] = []
        cursor: int | None = end_index
        while cursor is not None:
            path_indices.append(cursor)
            cursor = previous[cursor]
        path_indices.reverse()
        return [candidates[index] for index in path_indices]

    def segment_clear(self, a: Point, b: Point, obstacles: list[Obstacle]) -> bool:
        return all(
            self._distance_point_to_segment(obstacle.center, a, b)
            >= self.puck.center_clearance_mm - 1e-6
            for obstacle in obstacles
        )

    def point_clear(self, point: Point, obstacles: list[Obstacle]) -> bool:
        return all(
            math.hypot(point.x_mm - obstacle.center.x_mm, point.y_mm - obstacle.center.y_mm)
            >= self.puck.center_clearance_mm - 1e-6
            for obstacle in obstacles
        )

    def _candidate_points(self, start: Point, end: Point, obstacles: list[Obstacle]) -> list[Point]:
        points = [start, end]
        step = self.config.square_size_mm

        for col in range(self.config.table_columns):
            for row in range(self.config.table_rows):
                point = Point((col + 0.5) * step, (row + 0.5) * step)
                if self.point_clear(point, obstacles):
                    points.append(point)

        min_x = self.puck.puck_radius_mm
        max_x = self.config.table_width_mm - self.puck.puck_radius_mm
        min_y = self.puck.puck_radius_mm
        max_y = self.config.table_height_mm - self.puck.puck_radius_mm
        for col in range(1, self.config.table_columns):
            for row in range(1, self.config.table_rows):
                point = Point(col * step, row * step)
                if min_x <= point.x_mm <= max_x and min_y <= point.y_mm <= max_y:
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

