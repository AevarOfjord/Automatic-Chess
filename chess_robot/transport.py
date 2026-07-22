from __future__ import annotations

import time
from abc import ABC, abstractmethod

import serial

from .logging_config import get_logger
from .protocol import Action, ArmCommand, ArmResponse, Status

log = get_logger("transport")


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
        self.commands: list[ArmCommand] = []
        self.fail_next: str | None = None
        self.stopped = False

    def exchange(self, command: ArmCommand, timeout_s: float) -> ArmResponse:
        if command.command_id in self.completed:
            return self.completed[command.command_id]
        self.commands.append(command)
        self.execution_count[command.command_id] = self.execution_count.get(command.command_id, 0) + 1
        if self.fail_next:
            detail, self.fail_next = self.fail_next, None
            response = ArmResponse(command.command_id, command.arm, Status.FAULT, detail)
            log.warning("mock fault injected: %s %s", command.action.value, detail)
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
    def __init__(self, port: str, baudrate: int = 115200, serial_obj: object | None = None) -> None:
        # ``serial_obj`` lets tests inject a fake port (a duck-typed object with
        # write/readline/close); production opens the real device and lets the
        # ESP32 finish its USB reset before the input buffer is flushed.
        if serial_obj is not None:
            self.serial = serial_obj
            return
        log.info("opening serial gateway %s @ %s", port, baudrate)
        self.serial = serial.Serial(port, baudrate, timeout=0.2)
        time.sleep(2.0)
        self.serial.reset_input_buffer()

    def exchange(self, command: ArmCommand, timeout_s: float) -> ArmResponse:
        wire = command.to_wire()
        log.debug("TX %s", wire.decode("utf-8", errors="replace").strip())
        self.serial.write(wire)
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            raw = self.serial.readline()
            if not raw:
                continue
            try:
                response = ArmResponse.from_wire(raw)
            except (ValueError, KeyError):
                log.debug("ignored non-JSON serial line: %r", raw[:80])
                continue
            if response.command_id != command.command_id:
                log.debug(
                    "ignored response for other command id=%s want=%s",
                    response.command_id,
                    command.command_id,
                )
                continue
            if response.status in {Status.DONE, Status.FAULT}:
                log.debug("RX %s %s", response.status.value, response.detail)
                return response
        log.error(
            "gateway timeout after %.1fs waiting for %s %s",
            timeout_s,
            command.arm.value,
            command.action.value,
        )
        return ArmResponse(command.command_id, command.arm, Status.FAULT, "gateway timeout")

    def close(self) -> None:
        log.info("closing serial gateway")
        self.serial.close()
