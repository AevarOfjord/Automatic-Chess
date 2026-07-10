from __future__ import annotations

import unittest

from chess_robot.config import ArmId, RobotConfig
from chess_robot.geometry import BoardLayout, ScaraKinematics
from chess_robot.geometry_optimizer import GeometryDesign, evaluate_design
from chess_robot.visual_simulator import VisualChessRobotSimulator, VisualOptions


class GeometryOptimizerTests(unittest.TestCase):
    def test_default_joint_windows_are_exactly_270_degrees(self) -> None:
        config = RobotConfig()
        for arm in ArmId:
            arm_config = config.arm(arm)
            self.assertEqual(arm_config.shoulder_limits_deg[1] - arm_config.shoulder_limits_deg[0], 270.0)
            self.assertEqual(arm_config.elbow_limits_deg[1] - arm_config.elbow_limits_deg[0], 270.0)

    def test_wrapped_elbow_solution_is_used_inside_motor_window(self) -> None:
        config = RobotConfig()
        point = BoardLayout(config).square("d4")
        result = ScaraKinematics(config.white_arm).inverse(point)
        self.assertTrue(result.reachable)
        assert result.pose is not None
        self.assertGreaterEqual(result.pose.elbow_deg, -345.0)
        self.assertLessEqual(result.pose.elbow_deg, -75.0)

    def test_design_builds_a_true_180_degree_mirror(self) -> None:
        design = GeometryDesign(270, 270, 0, 250, 60, -135, -345)
        config = design.config()
        self.assertEqual(config.white_arm.base_x_mm, -config.black_arm.base_x_mm)
        self.assertEqual(config.white_arm.base_y_mm, -config.black_arm.base_y_mm)
        self.assertEqual(config.white_arm.link_1_mm, config.black_arm.link_1_mm)
        self.assertEqual(config.white_arm.link_2_mm, config.black_arm.link_2_mm)
        self.assertEqual(config.black_arm.forward_angle_deg - config.white_arm.forward_angle_deg, 180.0)

    def test_selected_geometry_certifies_full_table_and_grid_routes(self) -> None:
        design = GeometryDesign(270, 270, 0, 250, 60, -135, -345)
        report = evaluate_design(design, grid_mm=1)
        self.assertTrue(report.valid, report.worst_location)
        self.assertEqual(report.unreachable_points, 0)
        self.assertEqual(report.checked_routes, 652)
        self.assertGreaterEqual(report.min_joint_headroom_deg, 15.0)
        self.assertGreaterEqual(report.min_singularity_distance_deg, 15.0)

    def test_simulator_starts_with_both_arms_folded_at_home(self) -> None:
        simulator = VisualChessRobotSimulator(options=VisualOptions(auto_start=False))
        try:
            for arm_id in ArmId:
                arm = simulator.arms[arm_id]
                cfg = simulator.config.arm(arm_id)
                self.assertEqual(arm.pose.elbow_deg, cfg.home_elbow_deg)
                self.assertAlmostEqual(arm.tool.x_mm, cfg.base_x_mm)
                self.assertAlmostEqual(arm.tool.y_mm, cfg.base_y_mm)
                base, elbow, _ = simulator.forward_kinematics(arm_id, arm.pose)
                self.assertAlmostEqual(elbow.y_mm, base.y_mm)
                if arm_id is ArmId.WHITE:
                    self.assertGreater(elbow.x_mm, base.x_mm)
                else:
                    self.assertLess(elbow.x_mm, base.x_mm)
        finally:
            simulator.close()


if __name__ == "__main__":
    unittest.main()
