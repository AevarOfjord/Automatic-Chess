"""Compatibility wrapper around the trajectory-based dual-arm controller."""

from chess_robot.config import ArmId, RobotConfig
from chess_robot.hardware import DualArmHardware


class DualRobotController(DualArmHardware):
    def __init__(self, port="COM3", baudrate=115200, use_mock=True):
        config = RobotConfig(serial_port=port, serial_baudrate=baudrate)
        super().__init__(config=config, use_mock=use_mock)

    def calibrate(self):
        self.home_all()
        return True

    def move_piece(self, arm_id, from_square, to_square):
        self.transfer(ArmId(arm_id), f"board:{from_square}", f"board:{to_square}")
