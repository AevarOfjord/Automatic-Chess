from __future__ import annotations

import unittest

from chess_robot.config import ArmId, RobotConfig
from chess_robot.geometry import BoardLayout, JointPose, ScaraKinematics
from chess_robot.geometry_optimizer import GeometryDesign, JOINT_WINDOW_DEG, evaluate_design
from chess_robot.visual_simulator import VisualChessRobotSimulator, VisualOptions


class GeometryOptimizerTests(unittest.TestCase):
    def test_default_joint_windows_are_exactly_180_degrees(self) -> None:
        config = RobotConfig()
        for arm in ArmId:
            arm_config = config.arm(arm)
            self.assertEqual(
                arm_config.shoulder_limits_deg[1] - arm_config.shoulder_limits_deg[0],
                JOINT_WINDOW_DEG,
            )
            self.assertEqual(
                arm_config.elbow_limits_deg[1] - arm_config.elbow_limits_deg[0],
                JOINT_WINDOW_DEG,
            )
            self.assertEqual(
                arm_config.wrist_limits_deg[1] - arm_config.wrist_limits_deg[0],
                JOINT_WINDOW_DEG,
            )

    def test_default_links_are_unequal_three_segment(self) -> None:
        config = RobotConfig()
        for arm in ArmId:
            cfg = config.arm(arm)
            self.assertEqual(cfg.link_1_mm, 200.0)
            self.assertEqual(cfg.link_2_mm, 160.0)
            self.assertEqual(cfg.link_3_mm, 180.0)
            self.assertNotEqual(cfg.link_1_mm, cfg.link_2_mm)
            self.assertNotEqual(cfg.link_2_mm, cfg.link_3_mm)

    def test_wrapped_solution_stays_inside_motor_windows(self) -> None:
        config = RobotConfig()
        point = BoardLayout(config).square("d4")
        result = ScaraKinematics(config.white_arm).inverse(point)
        self.assertTrue(result.reachable, result.reason)
        assert result.pose is not None
        self.assertGreaterEqual(result.pose.shoulder_deg, -90.0)
        self.assertLessEqual(result.pose.shoulder_deg, 90.0)
        self.assertGreaterEqual(result.pose.elbow_deg, 0.0)
        self.assertLessEqual(result.pose.elbow_deg, 180.0)
        self.assertGreaterEqual(result.pose.wrist_deg, 0.0)
        self.assertLessEqual(result.pose.wrist_deg, 180.0)

    def test_fk_inverse_round_trip_near_center(self) -> None:
        config = RobotConfig()
        point = BoardLayout(config).square("e4")
        solver = ScaraKinematics(config.white_arm)
        result = solver.inverse(point)
        self.assertTrue(result.reachable)
        assert result.pose is not None
        *_, tool = solver.forward(result.pose)
        self.assertAlmostEqual(tool.x_mm, point.x_mm, delta=1.5)
        self.assertAlmostEqual(tool.y_mm, point.y_mm, delta=1.5)

    def test_design_builds_a_true_180_degree_mirror(self) -> None:
        design = GeometryDesign(200, 160, 180, 0, 250, 45, -90, 0, 0)
        config = design.config()
        self.assertEqual(config.white_arm.base_x_mm, -config.black_arm.base_x_mm)
        self.assertEqual(config.white_arm.base_y_mm, -config.black_arm.base_y_mm)
        self.assertEqual(config.white_arm.link_1_mm, config.black_arm.link_1_mm)
        self.assertEqual(config.white_arm.link_2_mm, config.black_arm.link_2_mm)
        self.assertEqual(config.white_arm.link_3_mm, config.black_arm.link_3_mm)
        self.assertEqual(config.black_arm.forward_angle_deg - config.white_arm.forward_angle_deg, 180.0)

    def test_selected_geometry_certifies_full_table_and_grid_routes(self) -> None:
        design = GeometryDesign(200, 160, 180, 0, 250, 45, -90, 0, 0)
        # 5 mm grid keeps the suite fast while still exercising continuous paths.
        report = evaluate_design(design, grid_mm=5, route_step_mm=10)
        self.assertTrue(report.valid, report.worst_location)
        self.assertEqual(report.unreachable_points, 0)
        # 14×8 grid neighbor routes checked once per arm.
        self.assertEqual(report.checked_routes, 768)
        self.assertGreaterEqual(report.min_joint_headroom_deg, 5.0)
        self.assertGreaterEqual(report.min_singularity_distance_deg, 5.0)

    def test_default_robot_config_matches_certified_14_col_geometry(self) -> None:
        config = RobotConfig()
        self.assertEqual(config.table_columns, 14)
        self.assertEqual(config.white_arm.link_1_mm, 200.0)
        self.assertEqual(config.white_arm.link_2_mm, 160.0)
        self.assertEqual(config.white_arm.link_3_mm, 180.0)
        self.assertEqual(config.white_arm.forward_angle_deg, 45.0)
        self.assertEqual(config.black_arm.forward_angle_deg, -135.0)

    def test_joint_pose_wire_includes_three_joints(self) -> None:
        wire = JointPose(10.0, 20.0, 30.0).as_wire(0.0)
        self.assertEqual(wire[:4], [10.0, 20.0, 30.0, 0.0])
        self.assertEqual(len(wire), 6)

    def test_simulator_starts_with_both_arms_folded_at_home(self) -> None:
        simulator = VisualChessRobotSimulator(options=VisualOptions(auto_start=False))
        try:
            for arm_id in ArmId:
                arm = simulator.arms[arm_id]
                cfg = simulator.config.arm(arm_id)
                self.assertEqual(arm.pose.shoulder_deg, cfg.home_shoulder_deg)
                self.assertEqual(arm.pose.elbow_deg, cfg.home_elbow_deg)
                self.assertEqual(arm.pose.wrist_deg, cfg.home_wrist_deg)
                base, elbow, wrist, tool = simulator.forward_kinematics(arm_id, arm.pose)
                self.assertAlmostEqual(arm.tool.x_mm, tool.x_mm, places=3)
                self.assertAlmostEqual(arm.tool.y_mm, tool.y_mm, places=3)
                # Folded rest lies parallel to the long table edge (constant y = base y).
                self.assertAlmostEqual(tool.y_mm, cfg.base_y_mm, places=5)
                self.assertAlmostEqual(elbow.y_mm, cfg.base_y_mm, places=5)
                self.assertAlmostEqual(wrist.y_mm, cfg.base_y_mm, places=5)
                if arm_id is ArmId.WHITE:
                    self.assertGreater(tool.x_mm, base.x_mm)
                else:
                    self.assertLess(tool.x_mm, base.x_mm)
        finally:
            simulator.close()


if __name__ == "__main__":
    unittest.main()
