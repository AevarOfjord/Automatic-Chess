"""Deprecated ad-hoc script. Prefer package CLI + unittest.

    python -m chess_robot simulate --random --games 1 --max-plies 40
    python -m unittest discover -v
"""

from __future__ import annotations

import warnings

warnings.warn(
    "test_simulation.py is deprecated; use python -m chess_robot simulate and unittest",
    DeprecationWarning,
    stacklevel=2,
)

if __name__ == "__main__":
    from chess_robot.game import GameManager

    manager = GameManager(use_mock_hardware=True, use_random_players=True, seed=1)
    manager.initialize()
    try:
        result = manager.play_game(vary_opening=False, max_plies=20)
        print(f"Simulation completed: {result} ({manager.board.ply()} plies)")
    finally:
        manager.close()
