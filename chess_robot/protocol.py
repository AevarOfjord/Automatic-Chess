from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from .config import ArmId

DEFAULT_JOURNAL_MAX_BYTES = 5_000_000


class Action(str, Enum):
    HOME = "HOME"
    EXECUTE_TRAJECTORY = "EXECUTE_TRAJECTORY"
    SET_MAGNET = "SET_MAGNET"
    PARK = "PARK"
    STATUS = "STATUS"
    STOP = "STOP"


class Status(str, Enum):
    ACCEPTED = "ACCEPTED"
    STARTED = "STARTED"
    DONE = "DONE"
    FAULT = "FAULT"
    RADIO_DELIVERED = "RADIO_DELIVERED"


def new_command_id() -> str:
    return uuid.uuid4().hex[:12]


@dataclass(frozen=True)
class ArmCommand:
    arm: ArmId
    action: Action
    payload: dict[str, Any] = field(default_factory=dict)
    command_id: str = field(default_factory=new_command_id)

    def to_wire(self, max_bytes: int = 240) -> bytes:
        encoded = (
            json.dumps(
                {
                    "id": self.command_id,
                    "arm": self.arm.value,
                    "action": self.action.value,
                    "payload": self.payload,
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(encoded) > max_bytes:
            raise ValueError(f"command is {len(encoded)} bytes; ESP-NOW limit is {max_bytes}")
        return encoded

    @classmethod
    def from_wire(cls, raw: bytes | str) -> "ArmCommand":
        data = json.loads(raw)
        return cls(
            arm=ArmId(data["arm"]),
            action=Action(data["action"]),
            payload=data.get("payload") or {},
            command_id=data["id"],
        )


@dataclass(frozen=True)
class ArmResponse:
    command_id: str
    arm: ArmId
    status: Status
    detail: str = ""
    telemetry: dict[str, Any] = field(default_factory=dict)

    def to_wire(self) -> bytes:
        encoded = (
            json.dumps(
                {
                    "id": self.command_id,
                    "arm": self.arm.value,
                    "status": self.status.value,
                    "detail": self.detail,
                    "telemetry": self.telemetry,
                },
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        return encoded

    @classmethod
    def from_wire(cls, raw: bytes | str) -> "ArmResponse":
        data = json.loads(raw)
        return cls(
            command_id=data["id"],
            arm=ArmId(data["arm"]),
            status=Status(data["status"]),
            detail=data.get("detail", ""),
            telemetry=data.get("telemetry") or {},
        )


class CommandJournal:
    """Append-only command log with simple size rotation."""

    def __init__(self, path: Path, max_bytes: int = DEFAULT_JOURNAL_MAX_BYTES):
        self.path = path
        self.max_bytes = max_bytes

    def _rotate_if_needed(self) -> None:
        if self.max_bytes <= 0 or not self.path.exists():
            return
        if self.path.stat().st_size < self.max_bytes:
            return
        rotated = self.path.with_name(self.path.name + ".1")
        if rotated.exists():
            rotated.unlink()
        self.path.replace(rotated)

    def record(self, event: str, value: ArmCommand | ArmResponse) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._rotate_if_needed()
        body = asdict(value)
        for key, item in list(body.items()):
            if isinstance(item, Enum):
                body[key] = item.value
        row = {"time": time.time(), "event": event, **body}
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, separators=(",", ":")) + "\n")
