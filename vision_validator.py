"""Deprecated compatibility wrapper. Prefer :mod:`chess_robot.vision`."""

from __future__ import annotations

import warnings

from chess_robot.vision import BoardVision

warnings.warn(
    "vision_validator.VisionValidator is deprecated; use chess_robot.vision.BoardVision",
    DeprecationWarning,
    stacklevel=2,
)


class VisionValidator(BoardVision):
    def calibrate_board(self):
        if not self.use_mock:
            self.calibrate_interactive()
        return True

    def verify_move(self, logical_board):
        return self.verify_expected(logical_board)
