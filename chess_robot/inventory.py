from __future__ import annotations

import copy
from dataclasses import dataclass, replace

import chess

from .config import ArmId

DEAD_SLOTS_PER_ARM = 16


def board_location(square: int | str) -> str:
    name = chess.square_name(square) if isinstance(square, int) else square
    return f"board:{name}"


def dead_location(arm: ArmId, index: int) -> str:
    if not 0 <= index < DEAD_SLOTS_PER_ARM:
        raise ValueError(f"dead-piece slot out of range: {index}")
    return f"dead:{arm.value}:{index}"


def dead_label(arm: ArmId, index: int) -> str:
    prefix = "W" if arm is ArmId.WHITE else "B"
    return f"{prefix}{index + 1}"


@dataclass(frozen=True)
class PieceToken:
    """One physical puck from a standard 32-piece set.

    ``piece_type`` is the physical home type (always ``P`` for a pawn puck).
    ``logical_type`` is what the PC chess state treats it as after promotion
    (for example ``Q``).  On reset, logical type is cleared back to the home type.
    """

    token_id: str
    color: ArmId
    piece_type: str
    original_square: str | None = None
    logical_type: str | None = None

    @property
    def effective_type(self) -> str:
        return self.logical_type or self.piece_type


class PhysicalInventory:
    """Identity-preserving representation of a standard 32-piece chess set."""

    def __init__(self) -> None:
        self.tokens: dict[str, PieceToken] = {}
        self.locations: dict[str, str] = {}
        self._create_starting_pieces()

    def clone(self) -> "PhysicalInventory":
        return copy.deepcopy(self)

    def _create_starting_pieces(self) -> None:
        board = chess.Board()
        for square, piece in board.piece_map().items():
            color = ArmId.WHITE if piece.color is chess.WHITE else ArmId.BLACK
            square_name = chess.square_name(square)
            token_id = f"{color.value[0]}_{piece.symbol().upper()}_{square_name}"
            token = PieceToken(token_id, color, piece.symbol().upper(), original_square=square_name)
            self.tokens[token_id] = token
            self.locations[token_id] = board_location(square_name)

    @property
    def occupied(self) -> dict[str, str]:
        return {location: token_id for token_id, location in self.locations.items()}

    def token_at(self, location: str) -> PieceToken | None:
        token_id = self.occupied.get(location)
        return self.tokens.get(token_id) if token_id else None

    def location_of(self, token_id: str) -> str:
        return self.locations[token_id]

    def move(self, token_id: str, destination: str) -> None:
        occupant = self.occupied.get(destination)
        if occupant is not None and occupant != token_id:
            raise ValueError(f"{destination} is occupied by {occupant}")
        self.locations[token_id] = destination

    def set_logical_type(self, token_id: str, logical_type: str | None) -> None:
        token = self.tokens[token_id]
        self.tokens[token_id] = replace(token, logical_type=logical_type)

    def clear_promotions(self) -> None:
        for token_id, token in list(self.tokens.items()):
            if token.logical_type is not None:
                self.tokens[token_id] = replace(token, logical_type=None)

    def dead_rack_contents(self, arm: ArmId) -> list[str | None]:
        occupied = self.occupied
        return [occupied.get(dead_location(arm, index)) for index in range(DEAD_SLOTS_PER_ARM)]

    def first_empty_dead_slot(self, arm: ArmId) -> str:
        """Return the next deterministic out-of-play slot for this robot side.

        Captured pieces are never scattered randomly.  The capturing robot
        always fills its side line from W1/B1 upward, and the inventory map
        remembers exactly which physical token is in each slot for reset.
        """

        occupied = self.occupied
        for index in range(DEAD_SLOTS_PER_ARM):
            name = dead_location(arm, index)
            if name not in occupied:
                return name
        raise RuntimeError(f"{arm.value} dead-piece line is full")

    def first_empty_dead_label(self, arm: ArmId) -> str:
        location = self.first_empty_dead_slot(arm)
        return dead_label(arm, int(location.split(":")[2]))

    def first_empty_capture(self, arm: ArmId) -> str:
        """Backward-compatible name for the side dead-piece line."""
        return self.first_empty_dead_slot(arm)

    def board_occupancy(self) -> set[str]:
        return {
            location.split(":", 1)[1]
            for location in self.locations.values()
            if location.startswith("board:")
        }

    def assert_matches(self, board: chess.Board) -> None:
        """Check that puck occupancy matches the logical board squares.

        Piece *identity* after promotion is tracked via ``logical_type`` and
        the PC ``chess.Board``; physical pucks never change type.
        """

        expected = {chess.square_name(square) for square in board.piece_map()}
        actual = self.board_occupancy()
        if actual != expected:
            raise AssertionError(
                f"physical inventory mismatch; missing={sorted(expected - actual)}, "
                f"extra={sorted(actual - expected)}"
            )
