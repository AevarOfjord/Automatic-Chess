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
        config = RobotConfig()
        board_left = config.table_origin_x_mm + config.board_origin_x_mm
        board_right = board_left + config.board_size_mm
        self.assertLess(layout.dead_slot(ArmId.WHITE, 0).x_mm, board_left)
        self.assertGreater(layout.dead_slot(ArmId.BLACK, 0).x_mm, board_right)
        self.assertGreaterEqual(
            abs(layout.dead_slot(ArmId.WHITE, 0).y_mm - layout.dead_slot(ArmId.WHITE, 2).y_mm),
            50.0,
        )

    def test_table_has_twenty_mm_separators_and_centered_board(self) -> None:
        config = RobotConfig()
        layout = BoardLayout(config)

        self.assertEqual(config.separator_width_mm, 20.0)
        self.assertEqual(config.table_columns, 12)  # piece cells only
        self.assertEqual(config.table_rows, 8)
        self.assertEqual(config.table_width_mm, 640.0)
        self.assertEqual(config.table_height_mm, 400.0)
        self.assertEqual(config.table_origin_x_mm, -320.0)
        self.assertEqual(config.table_origin_y_mm, -200.0)
        self.assertEqual(config.board_origin_x_mm, 120.0)  # 100 rack + 20 gap
        # Chess stays centered; dead racks sit outside the 20 mm gaps.
        self.assertEqual(layout.square("a1").x_mm, -175.0)
        self.assertEqual(layout.square("h8").x_mm, 175.0)
        self.assertEqual(layout.dead_slot(ArmId.WHITE, 0).x_mm, -295.0)
        self.assertEqual(layout.dead_slot(ArmId.WHITE, 1).x_mm, -245.0)
        self.assertEqual(layout.dead_slot(ArmId.BLACK, 0).x_mm, 245.0)
        self.assertEqual(layout.dead_slot(ArmId.BLACK, 15).x_mm, 295.0)
        self.assertAlmostEqual(layout.separator_center_x(left=True), -210.0)
        self.assertAlmostEqual(layout.separator_center_x(left=False), 210.0)
        board_left = config.table_origin_x_mm + config.board_origin_x_mm
        white_rack_right = config.table_origin_x_mm + config.dead_rack_columns * config.square_size_mm
        self.assertAlmostEqual(board_left - white_rack_right, 20.0)

    def test_grid_cell_names_use_chess_and_rack_labels(self) -> None:
        layout = BoardLayout(RobotConfig())

        # Piece columns C1…C12 (20 mm separators are not piece columns).
        self.assertEqual(layout.column_label(0), "C1")
        self.assertEqual(layout.column_label(11), "C12")
        self.assertEqual(layout.row_label(0), "R1")
        self.assertEqual(layout.row_label(7), "R8")

        # Play area is piece columns C3–C10 = a…h.
        self.assertEqual(layout.chess_start_col, 2)
        self.assertEqual(layout.cell_label(2, 0), "a1")
        self.assertEqual(layout.cell_label(9, 7), "h8")
        self.assertEqual(layout.cell_label(5, 3), "d4")

        # Dead racks: W on C1–C2; B on C11–C12.
        self.assertEqual(layout.cell_label(0, 7), "W1")
        self.assertEqual(layout.cell_label(1, 7), "W2")
        self.assertEqual(layout.cell_label(0, 6), "W3")
        self.assertEqual(layout.cell_label(10, 7), "B1")
        self.assertEqual(layout.cell_label(11, 0), "B16")
        self.assertEqual(layout.dead_slot_at_cell(0, 7), (ArmId.WHITE, 0))
        self.assertIsNone(layout.dead_slot_at_cell(2, 0))

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
        token = plan.resulting_inventory.tokens["W_P_a2"]
        self.assertEqual(token.piece_type, "P")
        self.assertEqual(token.logical_type, "Q")
        self.assertFalse(any(transfer.reason.startswith("place promoted") for transfer in plan.transfers))
        reset = ResetPlanner().plan(plan.resulting_inventory)
        self.assertEqual(reset.resulting_inventory.location_of("W_P_a2"), board_location("a2"))
        self.assertIsNone(reset.resulting_inventory.tokens["W_P_a2"].logical_type)
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
