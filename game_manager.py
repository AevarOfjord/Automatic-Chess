"""Deprecated compatibility entrypoint. Prefer :mod:`chess_robot.game`."""

from __future__ import annotations

import warnings

from chess_robot.game import GameManager as _GameManager

warnings.warn(
    "game_manager.GameManager is deprecated; import chess_robot.game.GameManager instead",
    DeprecationWarning,
    stacklevel=2,
)


class GameManager(_GameManager):
    def __init__(self, use_dummy_engine=False, engine_path="stockfish.exe", render_callback=None):
        super().__init__(
            use_mock_hardware=True,
            use_random_players=use_dummy_engine,
            engine_path=engine_path,
            render_callback=render_callback,
        )

    def reset_for_new_game(self):
        return self.reset_board()

    def run_endless_loop(self):
        return self.run_endless()

    def close_all(self):
        return self.close()


if __name__ == "__main__":
    GameManager(use_dummy_engine=True).run_endless_loop()
