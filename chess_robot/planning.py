from __future__ import annotations

from dataclasses import dataclass

import chess

from .config import ArmId
from .inventory import DEAD_SLOTS_PER_ARM, PhysicalInventory, board_location, dead_location


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
            # Physical layout uses a professional 32-piece set only.  The pawn
            # puck stays on the promotion square; the PC records that this token
            # is currently acting as the promoted piece type.
            promotion_type = chess.piece_symbol(move.promotion).upper()
            pawn = state.token_at(board_location(to_name))
            if pawn is None:
                raise AssertionError("promoting pawn disappeared from physical inventory")
            if pawn.piece_type != "P":
                raise AssertionError(f"expected physical pawn for promotion, got {pawn.piece_type}")
            state.set_logical_type(pawn.token_id, promotion_type)

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
    """Returns all 32 physical tokens to their initial homes.

    Strategy: evacuate every non-home piece onto the side dead racks (clear the
    board), then place each token home.  That avoids late-cycle corridor blocks
    that appear when shuffling pieces directly on a crowded mid-game table.
    """

    def plan(self, inventory: PhysicalInventory) -> MovePlan:
        state = inventory.clone()
        transfers: list[PhysicalTransfer] = []

        originals = [token for token in state.tokens.values() if token.original_square]
        self._evacuate_to_dead_racks(state, transfers, originals)
        self._place_from_storage(state, transfers, originals)

        state.clear_promotions()
        expected = chess.Board()
        state.assert_matches(expected)
        return MovePlan(None, transfers, expected, state)

    def _evacuate_to_dead_racks(
        self,
        state: PhysicalInventory,
        transfers: list[PhysicalTransfer],
        originals: list,
    ) -> None:
        for token in originals:
            home = board_location(token.original_square or "")
            location = state.location_of(token.token_id)
            if location == home or location.startswith("dead:"):
                continue
            parking = self._first_empty_dead_any(state)
            if parking is None:
                arm = self._arm_for_source(location, token.color)
                parking = f"buffer:{arm.value}"
                if state.token_at(parking):
                    raise RuntimeError("no temporary storage left during reset evacuation")
            self._move(state, transfers, token.token_id, parking, "evacuate for reset")

    def _place_from_storage(
        self,
        state: PhysicalInventory,
        transfers: list[PhysicalTransfer],
        originals: list,
    ) -> None:
        while True:
            misplaced = [
                token
                for token in originals
                if state.location_of(token.token_id) != board_location(token.original_square or "")
            ]
            if not misplaced:
                return
            # Prefer pieces on the board-facing dead-column first so outer rack
            # pieces are not trapped behind neighbors (50 mm cells, 30 mm pucks).
            misplaced.sort(
                key=lambda token: self._storage_exit_priority(state.location_of(token.token_id))
            )
            progressed = False
            occupied = state.occupied
            for token in misplaced:
                target = board_location(token.original_square or "")
                if target not in occupied:
                    self._move(
                        state,
                        transfers,
                        token.token_id,
                        target,
                        "restore starting square",
                    )
                    progressed = True
                    break
            if progressed:
                continue

            # Closed permutation among remaining homes: break via buffer.
            token = misplaced[0]
            arm = self._arm_for_source(state.location_of(token.token_id), token.color)
            buffer_location = f"buffer:{arm.value}"
            if state.token_at(buffer_location):
                raise RuntimeError(f"reset buffer unexpectedly occupied: {buffer_location}")
            self._move(state, transfers, token.token_id, buffer_location, "break reset cycle")

    @staticmethod
    def _first_empty_dead_any(state: PhysicalInventory) -> str | None:
        # Fill board-facing columns on BOTH arms before either outer column, so
        # approach corridors into the racks stay open longer.
        ordered: list[str] = []
        for col_phase in ("inner", "outer"):
            for arm in ArmId:
                inner_col = 1 if arm is ArmId.WHITE else 0
                col = inner_col if col_phase == "inner" else 1 - inner_col
                for index in range(DEAD_SLOTS_PER_ARM):
                    if index % 2 == col:
                        ordered.append(dead_location(arm, index))
        for name in ordered:
            if name not in state.occupied:
                return name
        return None

    @staticmethod
    def _storage_exit_priority(location: str) -> tuple[int, int]:
        """Lower tuples leave storage first."""
        parts = location.split(":")
        if parts[0] != "dead":
            return (0, 0)
        arm = ArmId(parts[1])
        index = int(parts[2])
        col = index % 2
        # White: col 1 faces the board; Black: col 0 faces the board.
        board_facing = (arm is ArmId.WHITE and col == 1) or (arm is ArmId.BLACK and col == 0)
        return (0 if board_facing else 1, index)

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
