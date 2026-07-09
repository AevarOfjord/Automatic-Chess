from __future__ import annotations

import unittest

import chess

from chess_robot.config import RobotConfig
from chess_robot.geometry import BoardLayout, Point
from chess_robot.inventory import PhysicalInventory
from chess_robot.trajectory import Obstacle, PuckModel, PuckTrajectoryPlanner, TrajectoryPlanningError


class PuckTrajectoryTests(unittest.TestCase):
    def test_diagonal_gap_between_four_pucks_is_clear_with_two_mm_clearance(self) -> None:
        planner = PuckTrajectoryPlanner(puck=PuckModel(puck_diameter_mm=30.0, clearance_mm=2.0))
        obstacles = [
            Obstacle("a", "test", Point(25, 25)),
            Obstacle("b", "test", Point(75, 25)),
            Obstacle("c", "test", Point(25, 75)),
            Obstacle("d", "test", Point(75, 75)),
        ]

        self.assertTrue(planner.point_clear(Point(50, 50), obstacles))

    def test_diagonal_gap_between_four_pucks_is_blocked_with_large_clearance(self) -> None:
        planner = PuckTrajectoryPlanner(puck=PuckModel(puck_diameter_mm=30.0, clearance_mm=6.0))
        obstacles = [
            Obstacle("a", "test", Point(25, 25)),
            Obstacle("b", "test", Point(75, 25)),
            Obstacle("c", "test", Point(25, 75)),
            Obstacle("d", "test", Point(75, 75)),
        ]

        self.assertFalse(planner.point_clear(Point(50, 50), obstacles))

    def test_straight_segment_through_occupied_puck_is_blocked(self) -> None:
        planner = PuckTrajectoryPlanner()
        obstacles = [Obstacle("blocker", "board:d4", Point(275, 175))]

        self.assertFalse(planner.segment_clear(Point(125, 175), Point(475, 175), obstacles))

    def test_planner_routes_around_blocker(self) -> None:
        planner = PuckTrajectoryPlanner()
        obstacles = [Obstacle("blocker", "board:d4", Point(275, 175))]

        path = planner.plan_points(Point(125, 175), Point(475, 175), obstacles)

        self.assertGreater(len(path), 2)
        for start, end in zip(path, path[1:]):
            self.assertTrue(planner.segment_clear(start, end, obstacles))

    def test_inventory_transfer_path_excludes_the_moving_token(self) -> None:
        config = RobotConfig()
        inventory = PhysicalInventory()
        planner = PuckTrajectoryPlanner(config=config, layout=BoardLayout(config))

        path = planner.plan_transfer(inventory, "W_P_e2", "board:e2", "board:e4")

        self.assertEqual(path.points[0], BoardLayout(config).square("e2"))
        self.assertEqual(path.points[-1], BoardLayout(config).square("e4"))
        self.assertNotIn("W_P_e2", {obstacle.token_id for obstacle in path.obstacles})


if __name__ == "__main__":
    unittest.main()

