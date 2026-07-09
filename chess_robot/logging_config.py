from __future__ import annotations

import logging
import sys

# Library default: no "No handlers could be found" spam; CLI enables real output.
logging.getLogger("chess_robot").addHandler(logging.NullHandler())


def configure_logging(verbose: bool = False) -> None:
    """Idempotent logging setup for CLI entrypoints."""

    root = logging.getLogger("chess_robot")
    root.handlers = [h for h in root.handlers if not isinstance(h, logging.NullHandler)]
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.setLevel(logging.DEBUG if verbose else logging.INFO)
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%H:%M:%S")
    )
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"chess_robot.{name}")
