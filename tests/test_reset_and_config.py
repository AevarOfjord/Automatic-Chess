from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import chess

from chess_robot.config import RobotConfig
from chess_robot.game import GameManager


class ConfigEnvTests(unittest.TestCase):
    def test_from_env_overrides_serial_settings(self) -> None:
        with mock.patch.dict(
            os.environ,
            {
                "CHESS_ROBOT_PORT": "COM9",
                "CHESS_ROBOT_BAUD": "57600",
                "CHESS_ROBOT_TIMEOUT_S": "12.5",
                "CHESS_ROBOT_RETRIES": "2",
                "CHESS_ROBOT_JOURNAL": "runtime_data/custom.jsonl",
            },
            clear=False,
        ):
            config = RobotConfig.from_env()
        self.assertEqual(config.serial_port, "COM9")
        self.assertEqual(config.serial_baudrate, 57600)
        self.assertEqual(config.response_timeout_s, 12.5)
        self.assertEqual(config.command_retries, 2)
        self.assertEqual(config.journal_path, Path("runtime_data/custom.jsonl"))


class MidgameResetTests(unittest.TestCase):
    def test_mock_game_reset_restores_start_position(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RobotConfig(journal_path=Path(tmp) / "j.jsonl")
            manager = GameManager(
                config=config,
                use_mock_hardware=True,
                use_random_players=True,
                seed=1,
            )
            manager.initialize()
            try:
                manager.play_game(vary_opening=True, max_plies=12)
                self.assertNotEqual(manager.board, chess.Board())
                manager.reset_board()
                self.assertEqual(manager.board, chess.Board())
                manager.inventory.assert_matches(chess.Board())
                for token in manager.inventory.tokens.values():
                    self.assertIsNone(token.logical_type)
                    self.assertEqual(
                        manager.inventory.location_of(token.token_id),
                        f"board:{token.original_square}",
                    )
            finally:
                manager.close()

    def test_select_executable_move_skips_blocked_knight_developments(self) -> None:
        """After 1.e4 e5, Ng1-f3 has no planar corridor through the pawn wall."""
        with tempfile.TemporaryDirectory() as tmp:
            config = RobotConfig(journal_path=Path(tmp) / "j.jsonl")
            manager = GameManager(
                config=config,
                use_mock_hardware=True,
                use_random_players=True,
                seed=0,
            )
            manager.initialize()
            try:
                for uci in ("e2e4", "e7e5"):
                    manager.execute_move(chess.Move.from_uci(uci))
                preferred = chess.Move.from_uci("g1f3")
                self.assertIn(preferred, manager.board.legal_moves)
                plan = manager.move_planner.plan(manager.board, manager.inventory, preferred)
                self.assertFalse(manager.plan_is_pathable(plan))
                chosen = manager.select_executable_move(preferred)
                self.assertNotEqual(chosen, preferred)
                self.assertIn(chosen, manager.board.legal_moves)
            finally:
                manager.close()


if __name__ == "__main__":
    unittest.main()
