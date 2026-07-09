from __future__ import annotations

import unittest

from chess_robot.visual_simulator import VisualChessRobotSimulator, VisualOptions


class VisualSimulatorTests(unittest.TestCase):
    def test_visual_simulator_advances_moves_and_resets(self) -> None:
        simulator = VisualChessRobotSimulator(options=VisualOptions(seed=3, max_plies=4, speed=10.0))

        for _ in range(2500):
            simulator.tick(0.05)

        self.assertGreaterEqual(simulator.stats.completed_transfers, 4)
        self.assertGreaterEqual(simulator.stats.game_number, 1)
        self.assertLessEqual(simulator.stats.plies, 4)
        self.assertTrue(simulator.stats.last_move)

    def test_step_mode_stays_paused_until_requested(self) -> None:
        simulator = VisualChessRobotSimulator(options=VisualOptions(seed=4, auto_start=False))

        simulator.tick(1.0)
        self.assertEqual(simulator.stats.completed_transfers, 0)

        simulator.request_single_step()
        for _ in range(300):
            simulator.tick(0.05)

        self.assertGreaterEqual(simulator.stats.completed_transfers, 1)
        self.assertTrue(simulator.paused)

    def test_transfer_animation_is_fixed_height_planar_motion(self) -> None:
        simulator = VisualChessRobotSimulator(options=VisualOptions(seed=5, auto_start=False))
        simulator.request_single_step()

        for _ in range(20):
            simulator.tick(0.05)
            if simulator.plan_queue:
                break

        labels = [step.label for step in simulator.plan_queue]
        z_values = {(step.start_z, step.end_z) for step in simulator.plan_queue}
        self.assertIn("magnet on / pickup", labels)
        self.assertTrue(any(label.startswith("planar carry") for label in labels))
        self.assertNotIn("lower tool", labels)
        self.assertNotIn("lift piece", labels)
        self.assertEqual(z_values, {(0.0, 0.0)})

    def test_dashboard_stats_track_san_and_plan_progress(self) -> None:
        simulator = VisualChessRobotSimulator(
            options=VisualOptions(seed=6, max_plies=2, speed=12.0, auto_start=True)
        )
        for _ in range(4000):
            simulator.tick(0.05)
            if simulator.stats.plies >= 1 and simulator.stats.moves_san:
                break
        self.assertGreaterEqual(simulator.stats.plies, 1)
        self.assertTrue(simulator.stats.moves_san)
        self.assertNotEqual(simulator.stats.last_move_san, "—")
        self.assertGreaterEqual(simulator.stats.plan_transfers_total, 0)
        simulator.close()

    def test_control_board_actions_change_runtime_state(self) -> None:
        simulator = VisualChessRobotSimulator(options=VisualOptions(seed=8, auto_start=False, speed=1.0))
        self.assertTrue(simulator.paused)
        simulator.resume()
        self.assertFalse(simulator.paused)
        simulator.pause()
        self.assertTrue(simulator.paused)
        simulator.set_speed(2.0)
        self.assertAlmostEqual(simulator.options.speed, 2.0)
        simulator.nudge_speed(2.0)
        self.assertAlmostEqual(simulator.options.speed, 4.0)
        simulator.toggle_auto_loop()
        self.assertFalse(simulator.options.auto_loop)
        simulator.toggle_show_paths()
        self.assertFalse(simulator.options.show_paths)
        simulator.request_single_step()
        for _ in range(50):
            simulator.tick(0.05)
            if simulator.plan_queue or simulator.active_step:
                break
        simulator.skip_animation()
        self.assertIsNone(simulator.active_step)
        simulator.close()


if __name__ == "__main__":
    unittest.main()
