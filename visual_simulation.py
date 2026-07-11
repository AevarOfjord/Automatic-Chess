"""Deprecated launcher. Prefer ``python -m chess_robot visual``."""

from __future__ import annotations

import argparse
import warnings

from chess_robot.game import (
    DEFAULT_BLACK_ELO,
    DEFAULT_BLACK_SKILL,
    DEFAULT_MOVE_TIME_S,
    DEFAULT_WHITE_ELO,
    DEFAULT_WHITE_SKILL,
)
from chess_robot.visual_simulator import run_visual_simulator

warnings.warn(
    "visual_simulation.py is deprecated; use python -m chess_robot visual",
    DeprecationWarning,
    stacklevel=2,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the dual-SCARA chess robot visual simulator")
    parser.add_argument("--seed", type=int, help="make the random game reproducible")
    parser.add_argument("--max-plies", type=int, help="reset after this many plies")
    parser.add_argument("--speed", type=float, default=1.0, help="animation speed multiplier")
    parser.add_argument("--paused", action="store_true", help="start paused; press N to step")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--engine", default="stockfish.exe", help="UCI engine executable")
    parser.add_argument("--random", action="store_true", help="use random legal moves instead of Stockfish")
    parser.add_argument("--white-elo", type=int, default=DEFAULT_WHITE_ELO)
    parser.add_argument("--black-elo", type=int, default=DEFAULT_BLACK_ELO)
    parser.add_argument("--white-skill", type=int, default=DEFAULT_WHITE_SKILL)
    parser.add_argument("--black-skill", type=int, default=DEFAULT_BLACK_SKILL)
    parser.add_argument("--move-time", type=float, default=DEFAULT_MOVE_TIME_S)
    parser.add_argument("--fullscreen", action="store_true", help="start fullscreen")
    parser.add_argument("--width", type=int, default=1280, help="window width (windowed)")
    parser.add_argument("--height", type=int, default=800, help="window height (windowed)")
    return parser


if __name__ == "__main__":
    run_visual_simulator(build_parser().parse_args())
