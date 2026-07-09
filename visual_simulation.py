from __future__ import annotations

import argparse

from chess_robot.visual_simulator import run_visual_simulator


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Launch the dual-SCARA chess robot visual simulator")
    parser.add_argument("--seed", type=int, help="make the random game reproducible")
    parser.add_argument("--max-plies", type=int, help="reset after this many plies")
    parser.add_argument("--speed", type=float, default=1.0, help="animation speed multiplier")
    parser.add_argument("--paused", action="store_true", help="start paused; press N to step")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--engine", default="stockfish.exe", help="UCI engine executable")
    parser.add_argument("--random", action="store_true", help="use random legal moves instead of Stockfish")
    parser.add_argument("--white-elo", type=int, default=1700)
    parser.add_argument("--black-elo", type=int, default=1450)
    parser.add_argument("--white-skill", type=int, default=10)
    parser.add_argument("--black-skill", type=int, default=6)
    parser.add_argument("--move-time", type=float, default=0.08, help="Stockfish think time per move in seconds")
    return parser


if __name__ == "__main__":
    run_visual_simulator(build_parser().parse_args())
