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

    Fast symbolic plan (path geometry is validated / recovered in hardware):

    1. Greedy: any token whose home is free is restored (board-facing rack first).
    2. Evacuate remaining board/buffer tokens onto dead racks / buffers.
    3. Repeat greedy placement; break home-permutation cycles via buffers.
    """

    def plan(self, inventory: PhysicalInventory) -> MovePlan:
        state = inventory.clone()
        transfers: list[PhysicalTransfer] = []
        originals = [token for token in state.tokens.values() if token.original_square]

        for _ in range(400):
            misplaced = self._misplaced(state, originals)
            if not misplaced:
                break
            if self._try_restore_free_home(state, transfers, misplaced):
                continue
            if self._try_evacuate_board_token(state, transfers, misplaced):
                continue
            if self._try_buffer_break(state, transfers, misplaced):
                continue
            raise RuntimeError("reset planner stuck without free homes or storage")
        else:
            raise RuntimeError("reset planner exceeded transfer budget")

        state.clear_promotions()
        expected = chess.Board()
        state.assert_matches(expected)
        return MovePlan(None, transfers, expected, state)

    def _misplaced(self, state: PhysicalInventory, originals: list) -> list:
        return [
            token
            for token in originals
            if state.location_of(token.token_id) != board_location(token.original_square or "")
        ]

    def _try_restore_free_home(
        self,
        state: PhysicalInventory,
        transfers: list[PhysicalTransfer],
        misplaced: list,
    ) -> bool:
        candidates = [
            token
            for token in misplaced
            if board_location(token.original_square or "") not in state.occupied
        ]
        if not candidates:
            return False
        candidates.sort(key=lambda token: self._move_priority(state.location_of(token.token_id)))
        token = candidates[0]
        self._move(
            state,
            transfers,
            token.token_id,
            board_location(token.original_square or ""),
            "restore starting square",
        )
        return True

    def _try_evacuate_board_token(
        self,
        state: PhysicalInventory,
        transfers: list[PhysicalTransfer],
        misplaced: list,
    ) -> bool:
        board_tokens = [
            token
            for token in misplaced
            if not state.location_of(token.token_id).startswith("dead:")
        ]
        board_tokens.sort(key=lambda token: self._move_priority(state.location_of(token.token_id)))
        for token in board_tokens:
            parking = self._first_empty_storage(state)
            if parking is None:
                continue
            self._move(state, transfers, token.token_id, parking, "evacuate for reset")
            return True
        return False

    def _try_buffer_break(
        self,
        state: PhysicalInventory,
        transfers: list[PhysicalTransfer],
        misplaced: list,
    ) -> bool:
        for token in misplaced:
            source = state.location_of(token.token_id)
            arm = self._arm_for_source(source, token.color)
            for candidate_arm in (arm, arm.opposite):
                buffer = f"buffer:{candidate_arm.value}"
                if state.token_at(buffer) is None and source != buffer:
                    self._move(state, transfers, token.token_id, buffer, "break reset cycle")
                    return True
        return False

    def _first_empty_storage(self, state: PhysicalInventory) -> str | None:
        for col_phase in ("inner", "outer"):
            for arm in ArmId:
                inner_col = 1 if arm is ArmId.WHITE else 0
                col = inner_col if col_phase == "inner" else 1 - inner_col
                for index in range(DEAD_SLOTS_PER_ARM):
                    if index % 2 != col:
                        continue
                    name = dead_location(arm, index)
                    if name not in state.occupied:
                        return name
        for arm in ArmId:
            buffer = f"buffer:{arm.value}"
            if state.token_at(buffer) is None:
                return buffer
        return None

    @staticmethod
    def _move_priority(location: str) -> tuple[int, int, int]:
        parts = location.split(":")
        if parts[0] == "buffer":
            return (1, 0, 0)
        if parts[0] != "dead":
            return (0, 0, 0)
        arm = ArmId(parts[1])
        index = int(parts[2])
        col = index % 2
        board_facing = (arm is ArmId.WHITE and col == 1) or (arm is ArmId.BLACK and col == 0)
        return (2, 0 if board_facing else 1, index)

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
