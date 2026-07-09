from __future__ import annotations

from dataclasses import dataclass

import chess

from .config import ArmId
from .inventory import PhysicalInventory, board_location


@dataclass(frozen=True)
class PhysicalTransfer:
    arm: ArmId
    token_id: str
    source: str
    destination: str
    reason: str


@dataclass
class MovePlan:
    move: chess.Move | None
    transfers: list[PhysicalTransfer]
    expected_board: chess.Board
    resulting_inventory: PhysicalInventory


def color_arm(color: chess.Color) -> ArmId:
    return ArmId.WHITE if color is chess.WHITE else ArmId.BLACK


class ChessMovePlanner:
    def plan(
        self, board: chess.Board, inventory: PhysicalInventory, move: chess.Move
    ) -> MovePlan:
        if move not in board.legal_moves:
            raise ValueError(f"illegal move in current position: {move.uci()}")
        state = inventory.clone()
        state.assert_matches(board)
        arm = color_arm(board.turn)
        transfers: list[PhysicalTransfer] = []
        from_name = chess.square_name(move.from_square)
        to_name = chess.square_name(move.to_square)

        if board.is_capture(move):
            if board.is_en_passant(move):
                captured_square = move.to_square - 8 if board.turn is chess.WHITE else move.to_square + 8
            else:
                captured_square = move.to_square
            self._transfer(
                state,
                transfers,
                arm,
                board_location(captured_square),
                state.first_empty_dead_slot(arm),
                "capture",
            )

        self._transfer(
            state,
            transfers,
            arm,
            board_location(from_name),
            board_location(to_name),
            "primary move",
        )

        if board.is_castling(move):
            if chess.square_file(move.to_square) == 6:
                rook_from = chess.square(7, chess.square_rank(move.from_square))
                rook_to = chess.square(5, chess.square_rank(move.from_square))
            else:
                rook_from = chess.square(0, chess.square_rank(move.from_square))
                rook_to = chess.square(3, chess.square_rank(move.from_square))
            self._transfer(
                state,
                transfers,
                arm,
                board_location(rook_from),
                board_location(rook_to),
                "castling rook",
            )

        if move.promotion:
            promotion_type = chess.piece_symbol(move.promotion).upper()
            pawn = state.token_at(board_location(to_name))
            if pawn is None:
                raise AssertionError("promoting pawn disappeared from physical inventory")
            # Physical layout uses a professional 32-piece set only.  The pawn
            # stays on the promotion square; the PC board state remembers that
            # the token is currently acting as the promoted piece.

        expected = board.copy(stack=True)
        expected.push(move)
        state.assert_matches(expected)
        return MovePlan(move, transfers, expected, state)

    @staticmethod
    def _transfer(
        state: PhysicalInventory,
        transfers: list[PhysicalTransfer],
        arm: ArmId,
        source: str,
        destination: str,
        reason: str,
    ) -> None:
        token = state.token_at(source)
        if token is None:
            raise ValueError(f"no piece at {source} for {reason}")
        state.move(token.token_id, destination)
        transfers.append(PhysicalTransfer(arm, token.token_id, source, destination, reason))


class ResetPlanner:
    """Returns all 32 physical tokens to their initial homes."""

    def plan(self, inventory: PhysicalInventory) -> MovePlan:
        state = inventory.clone()
        transfers: list[PhysicalTransfer] = []

        originals = [token for token in state.tokens.values() if token.original_square]
        while True:
            misplaced = [
                token
                for token in originals
                if state.location_of(token.token_id) != board_location(token.original_square or "")
            ]
            if not misplaced:
                break
            progressed = False
            occupied = state.occupied
            for token in misplaced:
                target = board_location(token.original_square or "")
                if target not in occupied:
                    self._move(state, transfers, token.token_id, target, "restore starting square")
                    progressed = True
                    break
            if progressed:
                continue

            # A closed permutation remains. Move one token to its arm's external buffer.
            token = misplaced[0]
            arm = self._arm_for_source(state.location_of(token.token_id), token.color)
            buffer_location = f"buffer:{arm.value}"
            if state.token_at(buffer_location):
                raise RuntimeError(f"reset buffer unexpectedly occupied: {buffer_location}")
            self._move(state, transfers, token.token_id, buffer_location, "break reset cycle")

        expected = chess.Board()
        state.assert_matches(expected)
        return MovePlan(None, transfers, expected, state)

    def _move(
        self,
        state: PhysicalInventory,
        transfers: list[PhysicalTransfer],
        token_id: str,
        destination: str,
        reason: str,
        arm: ArmId | None = None,
    ) -> None:
        token = state.tokens[token_id]
        source = state.location_of(token_id)
        arm = arm or self._arm_for_source(source, token.color)
        state.move(token_id, destination)
        transfers.append(PhysicalTransfer(arm, token_id, source, destination, reason))

    @staticmethod
    def _arm_for_source(source: str, token_color: ArmId) -> ArmId:
        parts = source.split(":")
        if parts[0] in {"dead", "capture", "buffer"}:
            return ArmId(parts[1])
        return token_color
