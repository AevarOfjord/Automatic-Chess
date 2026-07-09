"""Deprecated compatibility wrapper. Prefer :mod:`chess_robot.hardware`."""

from __future__ import annotations

import warnings

from chess_robot.config import ArmId, RobotConfig
from chess_robot.hardware import DualArmHardware

warnings.warn(
    "robot_controller.DualRobotController is deprecated; use chess_robot.hardware.DualArmHardware",
    DeprecationWarning,
    stacklevel=2,
)


class DualRobotController(DualArmHardware):
    def __init__(self, port="COM3", baudrate=115200, use_mock=True):
        config = RobotConfig(serial_port=port, serial_baudrate=baudrate)
        super().__init__(config=config, use_mock=use_mock)

    def calibrate(self):
        self.home_all()
        return True

    def move_piece(self, arm_id, from_square, to_square):
        self.transfer(ArmId(arm_id), f"board:{from_square}", f"board:{to_square}")
