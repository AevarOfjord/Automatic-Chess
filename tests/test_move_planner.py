from __future__ import annotations

import unittest

import chess

from chess_robot.config import ArmId
from chess_robot.inventory import PhysicalInventory, board_location, dead_location
from chess_robot.planning import ChessMovePlanner, ResetPlanner


class MovePlannerTests(unittest.TestCase):
    def test_illegal_move_is_rejected(self) -> None:
        board = chess.Board()
        inventory = PhysicalInventory()
        with self.assertRaises(ValueError):
            ChessMovePlanner().plan(board, inventory, chess.Move.from_uci("e2e5"))

    def test_kingside_castling_moves_king_and_rook(self) -> None:
        board = chess.Board()
        inventory = PhysicalInventory()
        planner = ChessMovePlanner()
        for uci in ("e2e4", "e7e5", "g1f3", "b8c6", "f1c4", "g8f6"):
            plan = planner.plan(board, inventory, chess.Move.from_uci(uci))
            board = plan.expected_board
            inventory = plan.resulting_inventory

        plan = planner.plan(board, inventory, chess.Move.from_uci("e1g1"))
        reasons = [transfer.reason for transfer in plan.transfers]
        self.assertEqual(reasons, ["primary move", "castling rook"])
        self.assertEqual(plan.resulting_inventory.location_of("W_K_e1"), board_location("g1"))
        self.assertEqual(plan.resulting_inventory.location_of("W_R_h1"), board_location("f1"))

    def test_queenside_castling_moves_king_and_rook(self) -> None:
        board = chess.Board()
        inventory = PhysicalInventory()
        planner = ChessMovePlanner()
        # Clear the queenside corridor without relying on a long book line.
        for uci in ("d2d4", "d7d5", "b1c3", "b8c6", "c1f4", "c8f5", "d1d2", "d8d7"):
            plan = planner.plan(board, inventory, chess.Move.from_uci(uci))
            board = plan.expected_board
            inventory = plan.resulting_inventory

        castle = chess.Move.from_uci("e1c1")
        self.assertIn(castle, board.legal_moves)
        plan = planner.plan(board, inventory, castle)
        reasons = [transfer.reason for transfer in plan.transfers]
        self.assertEqual(reasons, ["primary move", "castling rook"])
        self.assertEqual(plan.resulting_inventory.location_of("W_K_e1"), board_location("c1"))
        self.assertEqual(plan.resulting_inventory.location_of("W_R_a1"), board_location("d1"))

    def test_en_passant_capture_removes_pawn_from_passed_square(self) -> None:
        board = chess.Board()
        inventory = PhysicalInventory()
        planner = ChessMovePlanner()
        for uci in ("e2e4", "a7a6", "e4e5", "d7d5"):
            plan = planner.plan(board, inventory, chess.Move.from_uci(uci))
            board = plan.expected_board
            inventory = plan.resulting_inventory

        plan = planner.plan(board, inventory, chess.Move.from_uci("e5d6"))
        self.assertEqual(plan.transfers[0].reason, "capture")
        self.assertEqual(plan.transfers[0].source, board_location("d5"))
        self.assertEqual(plan.transfers[0].destination, dead_location(ArmId.WHITE, 0))
        self.assertEqual(plan.resulting_inventory.location_of("B_P_d7"), dead_location(ArmId.WHITE, 0))
        self.assertEqual(plan.resulting_inventory.location_of("W_P_e2"), board_location("d6"))

    def test_promotion_sets_logical_type_and_keeps_physical_pawn(self) -> None:
        board = chess.Board.empty()
        board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
        board.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
        board.set_piece_at(chess.A7, chess.Piece(chess.PAWN, chess.WHITE))
        board.turn = chess.WHITE
        board.castling_rights = 0
        inventory = self._sparse(
            {
                "W_K_e1": "board:e1",
                "B_K_e8": "board:e8",
                "W_P_a2": "board:a7",
            }
        )

        plan = ChessMovePlanner().plan(board, inventory, chess.Move.from_uci("a7a8q"))
        token = plan.resulting_inventory.tokens["W_P_a2"]
        self.assertEqual(token.piece_type, "P")
        self.assertEqual(token.logical_type, "Q")
        self.assertEqual(token.effective_type, "Q")
        self.assertEqual(plan.resulting_inventory.location_of("W_P_a2"), "board:a8")

        reset = ResetPlanner().plan(plan.resulting_inventory)
        restored = reset.resulting_inventory.tokens["W_P_a2"]
        self.assertIsNone(restored.logical_type)
        self.assertEqual(restored.effective_type, "P")
        self.assertEqual(reset.resulting_inventory.location_of("W_P_a2"), board_location("a2"))

    def _sparse(self, placements: dict[str, str]) -> PhysicalInventory:
        inventory = PhysicalInventory()
        next_dead = {ArmId.WHITE: 0, ArmId.BLACK: 0}
        for token_id, token in inventory.tokens.items():
            if token_id in placements:
                continue
            arm = token.color
            inventory.move(token_id, f"dead:{arm.value}:{next_dead[arm]}")
            next_dead[arm] += 1
        for token_id, destination in placements.items():
            inventory.move(token_id, destination)
        return inventory


if __name__ == "__main__":
    unittest.main()
