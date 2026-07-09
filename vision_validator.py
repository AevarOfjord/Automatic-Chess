"""Compatibility wrapper around occupancy-based board vision."""

from chess_robot.vision import BoardVision


class VisionValidator(BoardVision):
    def calibrate_board(self):
        if not self.use_mock:
            self.calibrate_interactive()
        return True

    def verify_move(self, logical_board):
        return self.verify_expected(logical_board)
