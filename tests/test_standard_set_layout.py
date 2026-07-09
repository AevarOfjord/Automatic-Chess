from __future__ import annotations

import unittest

import chess

from chess_robot.config import ArmId, RobotConfig
from chess_robot.geometry import BoardLayout, validate_layout, unreachable
from chess_robot.inventory import PhysicalInventory
from chess_robot.inventory import board_location, dead_label, dead_location
from chess_robot.planning import ChessMovePlanner, ResetPlanner


class StandardSetLayoutTests(unittest.TestCase):
    def test_inventory_is_exactly_one_standard_chess_set(self) -> None:
        inventory = PhysicalInventory()

        self.assertEqual(len(inventory.tokens), 32)
        self.assertTrue(all(token.original_square for token in inventory.tokens.values()))
        self.assertFalse(any(location.startswith("reserve:") for location in inventory.locations.values()))
        self.assertFalse(any(location.startswith("pawn_return:") for location in inventory.locations.values()))

    def test_dead_slots_are_marked_w1_to_w16_and_b1_to_b16(self) -> None:
        layout = BoardLayout(RobotConfig())

        self.assertEqual(layout.dead_slot_label(ArmId.WHITE, 0), "W1")
        self.assertEqual(layout.dead_slot_label(ArmId.WHITE, 15), "W16")
        self.assertEqual(layout.dead_slot_label(ArmId.BLACK, 0), "B1")
        self.assertEqual(layout.dead_slot_label(ArmId.BLACK, 15), "B16")
        self.assertEqual(dead_label(ArmId.WHITE, 0), "W1")
        self.assertEqual(dead_label(ArmId.BLACK, 15), "B16")
        self.assertLess(layout.dead_slot(ArmId.WHITE, 0).x_mm, RobotConfig().board_origin_x_mm)
        self.assertGreater(
            layout.dead_slot(ArmId.BLACK, 0).x_mm,
            RobotConfig().board_origin_x_mm + RobotConfig().board_size_mm,
        )
        self.assertGreaterEqual(
            abs(layout.dead_slot(ArmId.WHITE, 0).y_mm - layout.dead_slot(ArmId.WHITE, 2).y_mm),
            50.0,
        )

    def test_chessboard_is_centered_inside_twelve_by_eight_table_grid(self) -> None:
        config = RobotConfig()
        layout = BoardLayout(config)

        self.assertEqual(config.table_columns, 12)
        self.assertEqual(config.table_rows, 8)
        self.assertEqual(config.table_width_mm, 600.0)
        self.assertEqual(config.table_height_mm, 400.0)
        self.assertEqual(layout.square("a1").x_mm, 125.0)
        self.assertEqual(layout.square("h8").x_mm, 475.0)
        self.assertEqual(layout.dead_slot(ArmId.WHITE, 0).x_mm, 25.0)
        self.assertEqual(layout.dead_slot(ArmId.BLACK, 15).x_mm, 575.0)

    def test_capture_goes_to_capturing_arm_dead_line(self) -> None:
        board = chess.Board()
        inventory = PhysicalInventory()
        planner = ChessMovePlanner()

        for uci in ("e2e4", "d7d5"):
            plan = planner.plan(board, inventory, chess.Move.from_uci(uci))
            board = plan.expected_board
            inventory = plan.resulting_inventory

        plan = planner.plan(board, inventory, chess.Move.from_uci("e4d5"))

        self.assertEqual(plan.transfers[0].reason, "capture")
        self.assertEqual(plan.transfers[0].destination, "dead:WHITE:0")
        self.assertEqual(BoardLayout(RobotConfig()).dead_slot_label(ArmId.WHITE, 0), "W1")

    def test_captures_fill_dead_slots_in_fixed_order_and_reset_uses_identity(self) -> None:
        board = chess.Board()
        inventory = PhysicalInventory()
        planner = ChessMovePlanner()

        for uci in ("e2e4", "d7d5", "e4d5", "c7c6", "d5c6", "b7c6"):
            plan = planner.plan(board, inventory, chess.Move.from_uci(uci))
            board = plan.expected_board
            inventory = plan.resulting_inventory

        self.assertEqual(inventory.location_of("B_P_d7"), dead_location(ArmId.WHITE, 0))
        self.assertEqual(inventory.location_of("B_P_c7"), dead_location(ArmId.WHITE, 1))
        self.assertEqual(inventory.location_of("W_P_e2"), dead_location(ArmId.BLACK, 0))
        self.assertEqual(inventory.dead_rack_contents(ArmId.WHITE)[:3], ["B_P_d7", "B_P_c7", None])
        self.assertEqual(inventory.dead_rack_contents(ArmId.BLACK)[:2], ["W_P_e2", None])

        reset = ResetPlanner().plan(inventory)

        self.assertEqual(reset.resulting_inventory.location_of("B_P_d7"), board_location("d7"))
        self.assertEqual(reset.resulting_inventory.location_of("B_P_c7"), board_location("c7"))
        self.assertEqual(reset.resulting_inventory.location_of("W_P_e2"), board_location("e2"))
        reset.resulting_inventory.assert_matches(chess.Board())

    def test_reachability_includes_side_dead_lines(self) -> None:
        failures = list(unreachable(validate_layout(RobotConfig())))

        self.assertEqual(failures, [])

    def test_promotion_keeps_same_physical_pawn_and_reset_restores_it(self) -> None:
        board = chess.Board.empty()
        board.set_piece_at(chess.E1, chess.Piece(chess.KING, chess.WHITE))
        board.set_piece_at(chess.E8, chess.Piece(chess.KING, chess.BLACK))
        board.set_piece_at(chess.A7, chess.Piece(chess.PAWN, chess.WHITE))
        board.turn = chess.WHITE
        board.castling_rights = 0
        inventory = self._inventory_matching_sparse_board(
            {
                "W_K_e1": "board:e1",
                "B_K_e8": "board:e8",
                "W_P_a2": "board:a7",
            }
        )

        plan = ChessMovePlanner().plan(board, inventory, chess.Move.from_uci("a7a8q"))

        self.assertEqual(len(plan.resulting_inventory.tokens), 32)
        self.assertEqual(plan.resulting_inventory.location_of("W_P_a2"), "board:a8")
        self.assertFalse(any(transfer.reason.startswith("place promoted") for transfer in plan.transfers))
        reset = ResetPlanner().plan(plan.resulting_inventory)
        self.assertEqual(reset.resulting_inventory.location_of("W_P_a2"), board_location("a2"))
        reset.resulting_inventory.assert_matches(chess.Board())

    def _inventory_matching_sparse_board(self, placements: dict[str, str]) -> PhysicalInventory:
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
