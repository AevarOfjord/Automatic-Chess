from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from chess_robot.config import ArmId, RobotConfig
from chess_robot.hardware import DualArmHardware, MotionFault
from chess_robot.inventory import PhysicalInventory
from chess_robot.protocol import (
    Action,
    ArmCommand,
    ArmResponse,
    CommandJournal,
    Status,
)
from chess_robot.transport import MockGatewayTransport


class ProtocolTests(unittest.TestCase):
    def test_command_wire_round_trip_stays_under_limit(self) -> None:
        command = ArmCommand(
            ArmId.WHITE,
            Action.EXECUTE_TRAJECTORY,
            {
                "p": [
                    [10.0, 20.0, 30.0, 0.0, 2400, 1200],
                    [11.0, 21.0, 31.0, 0.0, 2400, 1200],
                    [12.0, 22.0, 32.0, 0.0, 2400, 1200],
                    [13.0, 23.0, 33.0, 0.0, 2400, 1200],
                ]
            },
        )
        wire = command.to_wire(max_bytes=240)
        self.assertLessEqual(len(wire), 240)
        restored = ArmCommand.from_wire(wire)
        self.assertEqual(restored.arm, ArmId.WHITE)
        self.assertEqual(restored.action, Action.EXECUTE_TRAJECTORY)
        self.assertEqual(restored.command_id, command.command_id)

    def test_oversized_command_is_rejected(self) -> None:
        command = ArmCommand(
            ArmId.BLACK,
            Action.EXECUTE_TRAJECTORY,
            {"p": [[float(i), float(i), float(i), 0.0, 2400, 1200] for i in range(20)]},
        )
        with self.assertRaises(ValueError):
            command.to_wire(max_bytes=240)

    def test_response_wire_round_trip(self) -> None:
        response = ArmResponse("abc123", ArmId.WHITE, Status.DONE, telemetry={"pickup": True})
        restored = ArmResponse.from_wire(response.to_wire())
        self.assertEqual(restored.command_id, "abc123")
        self.assertTrue(restored.telemetry["pickup"])

    def test_journal_rotates_when_over_max_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "journal.jsonl"
            journal = CommandJournal(path, max_bytes=120)
            command = ArmCommand(ArmId.WHITE, Action.STATUS)
            for _ in range(30):
                journal.record("command", command)
            self.assertTrue(path.exists())
            rotated = path.with_name(path.name + ".1")
            self.assertTrue(rotated.exists())


class HardwareMockTests(unittest.TestCase):
    def test_mock_timeout_is_retried(self) -> None:
        transport = MockGatewayTransport()
        transport.fail_next = "gateway timeout"
        config = RobotConfig(command_retries=1, journal_path=Path(tempfile.mkdtemp()) / "j.jsonl")
        hardware = DualArmHardware(config=config, transport=transport, use_mock=True)
        hardware.home(ArmId.WHITE)
        self.assertGreaterEqual(sum(transport.execution_count.values()), 2)

    def test_permanent_fault_is_not_retried(self) -> None:
        transport = MockGatewayTransport()
        transport.fail_next = "estop pressed"
        config = RobotConfig(command_retries=3, journal_path=Path(tempfile.mkdtemp()) / "j.jsonl")
        hardware = DualArmHardware(config=config, transport=transport, use_mock=True)
        with self.assertRaises(MotionFault):
            hardware.home(ArmId.WHITE)
        # One failed attempt only (no retries for non-timeout faults).
        self.assertEqual(sum(1 for _ in transport.execution_count.values()), 1)

    def test_transfer_parks_opposite_arm_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            config = RobotConfig(journal_path=Path(tmp) / "j.jsonl")
            transport = MockGatewayTransport()
            hardware = DualArmHardware(config=config, transport=transport, use_mock=True)
            hardware.home_all()
            inventory = PhysicalInventory()
            token = inventory.token_at("board:e2")
            assert token is not None
            # Leave black arm "unparked" by clearing parked flag after a trajectory-like state.
            hardware.parked[ArmId.BLACK] = False
            hardware.last_pose[ArmId.BLACK] = hardware._pose(ArmId.BLACK, "board:e7")
            hardware.transfer(
                ArmId.WHITE,
                "board:e2",
                "board:e4",
                inventory=inventory,
                token_id=token.token_id,
            )
            self.assertTrue(hardware.parked[ArmId.WHITE])
            self.assertTrue(hardware.parked[ArmId.BLACK])
            magnet_commands = [
                command for command in transport.commands if command.action is Action.SET_MAGNET
            ]
            self.assertEqual(
                [command.payload for command in magnet_commands],
                [
                    {"on": True, "settle_ms": 500},
                    {"on": False, "settle_ms": 500},
                ],
            )


class GameFaultTests(unittest.TestCase):
    def test_vision_mismatch_faults_system(self) -> None:
        import chess

        from chess_robot.game import GameManager
        from chess_robot.vision import BoardVision

        vision = BoardVision(use_mock=True)
        vision.set_mock_result_once(False)
        with tempfile.TemporaryDirectory() as tmp:
            config = RobotConfig(journal_path=Path(tmp) / "j.jsonl")
            manager = GameManager(
                config=config,
                vision=vision,
                use_mock_hardware=True,
                use_random_players=True,
                seed=1,
            )
            manager.initialize()
            with self.assertRaises(RuntimeError):
                manager.execute_move(chess.Move.from_uci("e2e4"))
            self.assertTrue(manager.faulted)
            manager.close()


if __name__ == "__main__":
    unittest.main()
