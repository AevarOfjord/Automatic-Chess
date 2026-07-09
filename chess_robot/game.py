from __future__ import annotations

import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Protocol

import chess
import chess.engine

from .config import ArmId, RobotConfig
from .hardware import DualArmHardware, MotionFault
from .inventory import PhysicalInventory
from .planning import ChessMovePlanner, MovePlan, ResetPlanner
from .vision import BoardVision


class Player(Protocol):
    def choose_move(self, board: chess.Board) -> chess.Move:
        ...

    def close(self) -> None:
        ...


@dataclass(frozen=True)
class EngineProfile:
    name: str
    elo: int
    skill: int
    move_time_s: float


DEFAULT_PROFILES = (
    EngineProfile("Tactician", 1850, 14, 0.18),
    EngineProfile("Explorer", 1550, 8, 0.12),
)


class RandomPlayer:
    def __init__(self, seed: int | None = None) -> None:
        self.random = random.Random(seed)

    def choose_move(self, board: chess.Board) -> chess.Move:
        return self.random.choice(list(board.legal_moves))

    def close(self) -> None:
        pass


class StockfishPlayer:
    def __init__(self, executable: str | Path, profile: EngineProfile) -> None:
        self.profile = profile
        self.engine = chess.engine.SimpleEngine.popen_uci(str(executable))
        options: dict[str, int | bool] = {}
        if "UCI_LimitStrength" in self.engine.options:
            options["UCI_LimitStrength"] = True
        if "UCI_Elo" in self.engine.options:
            option = self.engine.options["UCI_Elo"]
            minimum = int(option.min or profile.elo)
            maximum = int(option.max or profile.elo)
            options["UCI_Elo"] = max(minimum, min(maximum, profile.elo))
        if "Skill Level" in self.engine.options:
            options["Skill Level"] = profile.skill
        if options:
            self.engine.configure(options)

    def choose_move(self, board: chess.Board) -> chess.Move:
        result = self.engine.play(board, chess.engine.Limit(time=self.profile.move_time_s))
        if result.move is None:
            raise RuntimeError(f"{self.profile.name} returned no move")
        return result.move

    def close(self) -> None:
        self.engine.quit()


OPENING_LINES = (
    ("e2e4", "e7e5", "g1f3", "b8c6"),
    ("d2d4", "d7d5", "c2c4", "e7e6"),
    ("c2c4", "e7e5", "b1c3", "g8f6"),
    ("g1f3", "d7d5", "g2g3", "g8f6"),
)


class GameManager:
    def __init__(
        self,
        *,
        config: RobotConfig | None = None,
        hardware: DualArmHardware | None = None,
        vision: BoardVision | None = None,
        use_mock_hardware: bool = True,
        use_random_players: bool = False,
        engine_path: str | Path = "stockfish.exe",
        seed: int | None = None,
        render_callback: Callable[[chess.Board], None] | None = None,
    ) -> None:
        self.config = config or RobotConfig()
        self.hardware = hardware or DualArmHardware(self.config, use_mock=use_mock_hardware)
        self.vision = vision or BoardVision(use_mock=use_mock_hardware)
        self.inventory = PhysicalInventory()
        self.board = chess.Board()
        self.move_planner = ChessMovePlanner()
        self.reset_planner = ResetPlanner()
        self.render_callback = render_callback
        self.random = random.Random(seed)
        self.game_number = 0
        self.faulted = False
        self.last_fault = ""
        if use_random_players:
            self.players: tuple[Player, Player] = (RandomPlayer(seed), RandomPlayer(None if seed is None else seed + 1))
        else:
            self.players = tuple(StockfishPlayer(engine_path, profile) for profile in DEFAULT_PROFILES)  # type: ignore[assignment]

    def initialize(self, home: bool = True) -> None:
        if home:
            self.hardware.home_all()
        if not self.vision.use_mock and self.vision.homography is None:
            raise RuntimeError("load or perform camera calibration before starting hardware play")

    def _player_for_turn(self) -> Player:
        # Profiles swap colors every game.
        white_index = self.game_number % 2
        return self.players[white_index if self.board.turn is chess.WHITE else 1 - white_index]

    def execute_plan(self, plan: MovePlan) -> None:
        physical_state = self.inventory.clone()
        try:
            for transfer in plan.transfers:
                self.hardware.transfer(
                    transfer.arm,
                    transfer.source,
                    transfer.destination,
                    inventory=physical_state,
                    token_id=transfer.token_id,
                )
                physical_state.move(transfer.token_id, transfer.destination)
            self.hardware.park_all()
        except MotionFault as exc:
            self._fault(str(exc))
            raise
        if not self.vision.verify_expected(plan.expected_board):
            message = (
                f"camera mismatch; missing={sorted(self.vision.last_missing)}, "
                f"extra={sorted(self.vision.last_extra)}"
            )
            self._fault(message)
            raise RuntimeError(message)
        self.inventory = plan.resulting_inventory
        self.board = plan.expected_board
        if self.render_callback:
            self.render_callback(self.board.copy())

    def execute_move(self, move: chess.Move) -> None:
        if self.faulted:
            raise RuntimeError(f"system is faulted: {self.last_fault}")
        self.execute_plan(self.move_planner.plan(self.board, self.inventory, move))

    def play_opening(self) -> tuple[str, ...]:
        line = self.random.choice(OPENING_LINES)
        for uci in line:
            move = chess.Move.from_uci(uci)
            if move not in self.board.legal_moves:
                break
            self.execute_move(move)
        return line

    def play_game(self, vary_opening: bool = True, max_plies: int | None = None) -> str:
        if self.board != chess.Board():
            self.reset_board()
        if vary_opening:
            self.play_opening()
        plies = 0
        while not self.board.is_game_over(claim_draw=True):
            if max_plies is not None and plies >= max_plies:
                return "*"
            move = self._player_for_turn().choose_move(self.board)
            self.execute_move(move)
            plies += 1
        return self.board.result(claim_draw=True)

    def reset_board(self) -> None:
        self.execute_plan(self.reset_planner.plan(self.inventory))

    def run_endless(self, pause_s: float = 2.0) -> None:
        self.initialize()
        try:
            while True:
                result = self.play_game(vary_opening=True)
                print(f"Game {self.game_number + 1}: {result}")
                self.reset_board()
                self.game_number += 1
                time.sleep(pause_s)
        except KeyboardInterrupt:
            pass
        finally:
            self.hardware.stop_all()
            self.close()

    def _fault(self, detail: str) -> None:
        self.faulted = True
        self.last_fault = detail
        self.hardware.stop_all()

    def clear_fault_after_manual_inspection(self) -> None:
        self.hardware.home_all()
        if not self.vision.verify_expected(self.board):
            raise RuntimeError("cannot clear fault while camera and logical board disagree")
        self.faulted = False
        self.last_fault = ""

    def close(self) -> None:
        for player in self.players:
            player.close()
        self.hardware.close()
        self.vision.release()
