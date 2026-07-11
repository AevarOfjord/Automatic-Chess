from __future__ import annotations

import argparse
import dataclasses
from pathlib import Path

from .config import RobotConfig
from .game import (
    DEFAULT_BLACK_ELO,
    DEFAULT_BLACK_SKILL,
    DEFAULT_MOVE_TIME_S,
    DEFAULT_WHITE_ELO,
    DEFAULT_WHITE_SKILL,
    GameManager,
)
from .geometry import unreachable, validate_layout
from .geometry_optimizer import optimize_geometry, write_report
from .logging_config import configure_logging
from .vision import BoardVision
from .visual_simulator import run_visual_simulator


def reachability_command(_args: argparse.Namespace) -> int:
    report = validate_layout(RobotConfig.from_env())
    failures = list(unreachable(report))
    minimum_margin = min(
        result.singularity_margin
        for locations in report.values()
        for result in locations.values()
        if result.reachable
    )
    print(f"Checked {sum(len(locations) for locations in report.values())} required arm locations.")
    print(f"Minimum singularity margin: {minimum_margin:.3f}")
    if failures:
        for arm, name, result in failures:
            print(f"FAIL {arm.value} {name}: {result.reason}")
        return 1
    print("PASS: every board, dead-piece slot, buffer, and park location is reachable.")
    return 0


def manager_from_args(args: argparse.Namespace, mock: bool) -> GameManager:
    config = RobotConfig.from_env()
    if hasattr(args, "port") and args.port:
        config = dataclasses.replace(config, serial_port=args.port)
    vision = BoardVision(
        camera_index=getattr(args, "camera", 0),
        use_mock=mock,
    )
    calibration = getattr(args, "calibration", None)
    if calibration and not mock:
        vision.load_calibration(calibration)
    return GameManager(
        config=config,
        vision=vision,
        use_mock_hardware=mock,
        use_random_players=getattr(args, "random", False),
        engine_path=getattr(args, "engine", "stockfish.exe"),
        seed=getattr(args, "seed", None),
    )


def simulate_command(args: argparse.Namespace) -> int:
    from .hardware import MotionFault

    manager = manager_from_args(args, mock=True)
    manager.initialize()
    try:
        for game_index in range(args.games):
            result = manager.play_game(vary_opening=not args.no_openings, max_plies=args.max_plies)
            print(f"Simulation game {game_index + 1}: {result} ({manager.board.ply()} plies)")
            try:
                manager.reset_board()
            except (MotionFault, RuntimeError) as exc:
                print(f"Reset after game {game_index + 1} failed: {exc}")
                return 1
            manager.game_number += 1
            manager.faulted = False
            manager.last_fault = ""
    finally:
        manager.close()
    return 0


def run_command(args: argparse.Namespace) -> int:
    manager = manager_from_args(args, mock=False)
    manager.run_endless(pause_s=args.pause)
    return 0


def calibrate_command(args: argparse.Namespace) -> int:
    vision = BoardVision(camera_index=args.camera, use_mock=False)
    try:
        print("Click board corners in this order: top-left, top-right, bottom-right, bottom-left.")
        vision.calibrate_interactive()
        input("Remove every piece from the board, then press Enter to capture the empty reference...")
        vision.capture_empty_reference()
        vision.save_calibration(args.output)
        print(f"Saved camera calibration to {args.output}")
    finally:
        vision.release()
    return 0


def visual_command(args: argparse.Namespace) -> int:
    run_visual_simulator(args)
    return 0


def optimize_geometry_command(args: argparse.Namespace) -> int:
    result = optimize_geometry(
        coarse_grid_mm=args.coarse_grid_mm,
        final_grid_mm=args.final_grid_mm,
    )
    write_report(result, Path(args.output))
    design = result.design
    evaluation = result.evaluation
    print("PASS: certified mirrored 3R arm geometry (MG995 180°)")
    print(
        f"Links: {design.link_1_mm:.0f} / {design.link_2_mm:.0f} / "
        f"{design.link_3_mm:.0f} mm (unequal OK)"
    )
    print(
        "White base: "
        f"({-design.base_x_mm:.0f}, {-design.base_setback_mm:.0f}) mm; "
        f"heading {design.forward_angle_deg:.0f} deg"
    )
    print(
        "Joint windows (180° each): "
        f"shoulder {design.shoulder_window_start_deg:.0f}.."
        f"{design.shoulder_window_start_deg + 180:.0f}, "
        f"elbow {design.elbow_window_start_deg:.0f}.."
        f"{design.elbow_window_start_deg + 180:.0f}, "
        f"wrist {design.wrist_window_start_deg:.0f}.."
        f"{design.wrist_window_start_deg + 180:.0f} deg"
    )
    print(
        f"Minimum headroom: {evaluation.min_joint_headroom_deg:.1f} deg; "
        f"minimum singularity distance: {evaluation.min_singularity_distance_deg:.1f} deg"
    )
    print(f"Report: {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dual-SCARA chess robot controller")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="enable debug logging on stderr",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    reachability_parser = subparsers.add_parser("reachability", help="validate arm geometry")
    reachability_parser.set_defaults(function=reachability_command)

    optimize_parser = subparsers.add_parser(
        "optimize-geometry",
        help="search and certify the mirrored SCARA geometry",
    )
    optimize_parser.add_argument("--output", default="runtime_data/geometry_optimization.json")
    optimize_parser.add_argument("--coarse-grid-mm", type=int, default=50)
    optimize_parser.add_argument("--final-grid-mm", type=int, default=1)
    optimize_parser.set_defaults(function=optimize_geometry_command)

    simulate_parser = subparsers.add_parser("simulate", help="run the full stack with mock hardware")
    simulate_parser.add_argument("--games", type=int, default=1)
    simulate_parser.add_argument("--max-plies", type=int)
    simulate_parser.add_argument("--engine", default="stockfish.exe")
    simulate_parser.add_argument("--random", action="store_true")
    simulate_parser.add_argument("--seed", type=int)
    simulate_parser.add_argument("--no-openings", action="store_true")
    simulate_parser.set_defaults(function=simulate_command)

    visual_parser = subparsers.add_parser("visual", help="open the animated dual-SCARA visual simulator")
    visual_parser.add_argument("--seed", type=int)
    visual_parser.add_argument("--max-plies", type=int)
    visual_parser.add_argument("--speed", type=float, default=1.0)
    visual_parser.add_argument("--paused", action="store_true")
    visual_parser.add_argument("--fps", type=int, default=60)
    visual_parser.add_argument("--engine", default="stockfish.exe")
    visual_parser.add_argument("--random", action="store_true", help="use random legal moves instead of Stockfish")
    visual_parser.add_argument("--white-elo", type=int, default=DEFAULT_WHITE_ELO)
    visual_parser.add_argument("--black-elo", type=int, default=DEFAULT_BLACK_ELO)
    visual_parser.add_argument("--white-skill", type=int, default=DEFAULT_WHITE_SKILL)
    visual_parser.add_argument("--black-skill", type=int, default=DEFAULT_BLACK_SKILL)
    visual_parser.add_argument(
        "--move-time",
        type=float,
        default=DEFAULT_MOVE_TIME_S,
        help="Stockfish think time per move in seconds",
    )
    visual_parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="start in exclusive fullscreen (default is a resizable window)",
    )
    visual_parser.add_argument(
        "--width",
        type=int,
        default=1280,
        help="window width in pixels when not fullscreen (default 1280)",
    )
    visual_parser.add_argument(
        "--height",
        type=int,
        default=800,
        help="window height in pixels when not fullscreen (default 800)",
    )
    visual_parser.set_defaults(function=visual_command)

    default_port = RobotConfig.from_env().serial_port
    run_parser = subparsers.add_parser("run", help="run unattended games on physical hardware")
    run_parser.add_argument("--port", default=default_port)
    run_parser.add_argument("--camera", type=int, default=0)
    run_parser.add_argument("--calibration", default="runtime_data/camera_calibration.npz")
    run_parser.add_argument("--engine", default="stockfish.exe")
    run_parser.add_argument("--pause", type=float, default=2.0)
    run_parser.add_argument("--seed", type=int)
    run_parser.set_defaults(function=run_command)

    calibrate_parser = subparsers.add_parser("calibrate-camera", help="create camera calibration")
    calibrate_parser.add_argument("--camera", type=int, default=0)
    calibrate_parser.add_argument("--output", default="runtime_data/camera_calibration.npz")
    calibrate_parser.set_defaults(function=calibrate_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    configure_logging(verbose=getattr(args, "verbose", False))
    return args.function(args)


if __name__ == "__main__":
    raise SystemExit(main())
