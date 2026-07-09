"""PC-side control stack for the dual-SCARA chess robot."""

from .config import ArmId, RobotConfig
from .game import GameManager

__all__ = ["ArmId", "RobotConfig", "GameManager"]
