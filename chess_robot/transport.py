from __future__ import annotations

import time
from abc import ABC, abstractmethod

import serial

from .protocol import Action, ArmCommand, ArmResponse, Status


class GatewayTransport(ABC):
    @abstractmethod
    def exchange(self, command: ArmCommand, timeout_s: float) -> ArmResponse:
        raise NotImplementedError

    def close(self) -> None:
        pass


class MockGatewayTransport(GatewayTransport):
    """Deterministic hardware double with idempotency and fault injection."""

    def __init__(self) -> None:
        self.completed: dict[str, ArmResponse] = {}
        self.execution_count: dict[str, int] = {}
        self.fail_next: str | None = None
        self.stopped = False

    def exchange(self, command: ArmCommand, timeout_s: float) -> ArmResponse:
        if command.command_id in self.completed:
            return self.completed[command.command_id]
        self.execution_count[command.command_id] = self.execution_count.get(command.command_id, 0) + 1
        if self.fail_next:
            detail, self.fail_next = self.fail_next, None
            response = ArmResponse(command.command_id, command.arm, Status.FAULT, detail)
        elif self.stopped and command.action not in {Action.HOME, Action.STATUS}:
            response = ArmResponse(command.command_id, command.arm, Status.FAULT, "arm is stopped")
        else:
            if command.action is Action.STOP:
                self.stopped = True
            elif command.action is Action.HOME:
                self.stopped = False
            response = ArmResponse(
                command.command_id,
                command.arm,
                Status.DONE,
                telemetry={"pickup": bool(command.payload.get("on", False))},
            )
        self.completed[command.command_id] = response
        return response


class SerialGatewayTransport(GatewayTransport):
    def __init__(self, port: str, baudrate: int = 115200) -> None:
        self.serial = serial.Serial(port, baudrate, timeout=0.2)
        time.sleep(2.0)
        self.serial.reset_input_buffer()

    def exchange(self, command: ArmCommand, timeout_s: float) -> ArmResponse:
        self.serial.write(command.to_wire())
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = self.serial.readline()
            if not raw:
                continue
            try:
                response = ArmResponse.from_wire(raw)
            except (ValueError, KeyError):
                continue
            if response.command_id != command.command_id:
                continue
            if response.status in {Status.DONE, Status.FAULT}:
                return response
        return ArmResponse(command.command_id, command.arm, Status.FAULT, "gateway timeout")

    def close(self) -> None:
        self.serial.close()
