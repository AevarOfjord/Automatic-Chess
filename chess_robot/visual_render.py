from __future__ import annotations

import chess

from .config import ArmId
from .geometry import Point
from .visual_models import PIECE_SYMBOLS, Viewport


class PygameRenderer:
    def __init__(self, simulator, viewport: Viewport | None = None) -> None:
        import pygame

        self.pygame = pygame
        self.sim = simulator
        self.viewport = viewport or Viewport()
        pygame.init()
        self.screen = pygame.display.set_mode((self.viewport.width, self.viewport.height))
        pygame.display.set_caption("Dual-SCARA Chess Robot Digital Twin")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.SysFont("segoeui", 18)
        self.small = pygame.font.SysFont("segoeui", 14)
        self.piece_font = pygame.font.SysFont("segoeuisymbol", 25)

    def run(self) -> None:
        pygame = self.pygame
        running = True
        try:
            while running:
                dt_s = self.clock.tick(self.sim.options.fps) / 1000.0
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_q):
                            running = False
                        elif event.key == pygame.K_SPACE:
                            self.sim.toggle_pause()
                        elif event.key == pygame.K_n:
                            self.sim.request_single_step()
                        elif event.key == pygame.K_r:
                            self.sim.reset_now()
                        elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                            self.sim.options.speed = min(8.0, self.sim.options.speed * 1.25)
                        elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                            self.sim.options.speed = max(0.1, self.sim.options.speed / 1.25)
                self.sim.tick(dt_s)
                self.draw()
                pygame.display.flip()
        finally:
            self.sim.close()
            pygame.quit()

    def draw(self) -> None:
        self.screen.fill((24, 27, 33))
        self._draw_workspace()
        self._draw_storage()
        self._draw_board()
        self._draw_planned_paths()
        self._draw_pieces()
        self._draw_arms()
        self._draw_panel()

    def _draw_workspace(self) -> None:
        pygame = self.pygame
        board_rect = self._rect_from_world(Point(-45, -155), Point(645, 555))
        pygame.draw.rect(self.screen, (35, 39, 48), board_rect, border_radius=18)
        pygame.draw.rect(self.screen, (68, 75, 90), board_rect, width=2, border_radius=18)
        for arm in ArmId:
            base = Point(self.sim.config.arm(arm).base_x_mm, self.sim.config.arm(arm).base_y_mm)
            pygame.draw.circle(self.screen, (81, 92, 116), self.viewport.screen(base), self.viewport.length(18))
            label = "White robot base" if arm is ArmId.WHITE else "Black robot base"
            self._label(label, base, (180, 190, 210), dy=-28 if arm is ArmId.WHITE else 24)

    def _draw_board(self) -> None:
        pygame = self.pygame
        layout = self.sim.layout
        colors = ((236, 220, 188), (110, 145, 112))
        size = self.sim.config.square_size_mm
        chess_start_col = layout.chess_start_col
        chess_end_col = layout.chess_end_col
        for row_from_bottom in range(self.sim.config.table_rows):
            for table_col in range(self.sim.config.table_columns):
                world = Point(table_col * size, row_from_bottom * size)
                rect = self._rect_from_world(world, Point(world.x_mm + size, world.y_mm + size))
                cell_name = layout.cell_label(table_col, row_from_bottom)
                if chess_start_col <= table_col < chess_end_col:
                    chess_file = table_col - chess_start_col
                    pygame.draw.rect(self.screen, colors[(row_from_bottom + chess_file) % 2], rect)
                else:
                    pygame.draw.rect(self.screen, (50, 55, 66), rect)
                    pygame.draw.rect(self.screen, (70, 77, 92), rect, width=1)
                pygame.draw.circle(
                    self.screen,
                    (88, 96, 112),
                    self.viewport.screen(Point(world.x_mm + size / 2, world.y_mm + size / 2)),
                    self.viewport.length(3),
                )
                # One clear name per cell: a1…h8 on the board, W1…B16 on racks.
                label_color = (
                    (90, 100, 115)
                    if chess_start_col <= table_col < chess_end_col
                    else (170, 150, 130)
                )
                self._tiny(
                    cell_name,
                    Point(world.x_mm + size / 2, world.y_mm + size - 10),
                    label_color,
                )
        table_outline = self._rect_from_world(
            Point(0, 0), Point(self.sim.config.table_width_mm, self.sim.config.table_height_mm)
        )
        chess_outline = self._rect_from_world(
            Point(self.sim.config.board_origin_x_mm, 0),
            Point(
                self.sim.config.board_origin_x_mm + self.sim.config.board_size_mm,
                self.sim.config.board_size_mm,
            ),
        )
        pygame.draw.rect(self.screen, (18, 20, 25), table_outline, width=3)
        pygame.draw.rect(self.screen, (18, 20, 25), chess_outline, width=3)
        # Axis legends: C1…C12 left→right, R1…R8 bottom→top (matches chess ranks).
        for table_col in range(self.sim.config.table_columns):
            self._tiny(
                layout.column_label(table_col),
                Point((table_col + 0.5) * size, -16),
                (210, 218, 230),
            )
        for row_from_bottom in range(self.sim.config.table_rows):
            y = (row_from_bottom + 0.5) * size
            self._tiny(layout.row_label(row_from_bottom), Point(-22, y), (210, 218, 230))
        # Chess file/rank strip along the play area for quick orientation.
        for table_col in range(chess_start_col, chess_end_col):
            file_letter = chr(ord("a") + (table_col - chess_start_col))
            self._tiny(
                file_letter,
                Point((table_col + 0.5) * size, self.sim.config.table_height_mm + 16),
                (180, 190, 210),
            )
        for row_from_bottom in range(self.sim.config.board_squares):
            self._tiny(
                str(row_from_bottom + 1),
                Point(
                    self.sim.config.board_origin_x_mm + self.sim.config.board_size_mm + 14,
                    (row_from_bottom + 0.5) * size,
                ),
                (180, 190, 210),
            )

    def _draw_storage(self) -> None:
        pygame = self.pygame
        for arm in ArmId:
            for index in range(16):
                p = self.sim.layout.dead_slot(arm, index)
                pygame.draw.circle(
                    self.screen, (69, 51, 57), self.viewport.screen(p), self.viewport.length(14), width=2
                )
            rack_x = 50 if arm is ArmId.WHITE else 550
            self._label(
                f"{arm.value.title()} rack W1–W16" if arm is ArmId.WHITE else "Black rack B1–B16",
                Point(rack_x, 430),
                (170, 180, 200),
                dy=-8,
            )

    def _draw_planned_paths(self) -> None:
        pygame = self.pygame
        for path in self.sim.current_plan_paths[:6]:
            if len(path.points) < 2:
                continue
            screen_points = [self.viewport.screen(point) for point in path.points]
            pygame.draw.lines(self.screen, (245, 221, 111), False, screen_points, width=2)
            for point in screen_points[1:-1]:
                pygame.draw.circle(self.screen, (245, 221, 111), point, self.viewport.length(4))

    def _draw_pieces(self) -> None:
        held = {arm.held_token_id for arm in self.sim.arms.values() if arm.held_token_id}
        for token_id, location in self.sim.current_locations.items():
            if token_id in held:
                continue
            try:
                point = self.sim.layout.location(location)
            except ValueError:
                continue
            token = self.sim.inventory.tokens[token_id]
            piece_type = self._display_piece_type(token, location)
            self._draw_piece(point, piece_type, token.color, ghost=not location.startswith("board:"))
        for arm in self.sim.arms.values():
            if arm.held_token_id:
                token = self.sim.inventory.tokens[arm.held_token_id]
                self._draw_piece(arm.tool, token.effective_type, token.color, held=True)

    def _display_piece_type(self, token, location: str) -> str:
        if location.startswith("board:"):
            square_name = location.split(":", 1)[1]
            piece = self.sim.board.piece_at(chess.parse_square(square_name))
            if piece is not None:
                return piece.symbol().upper()
        return token.effective_type

    def _draw_piece(
        self,
        point: Point,
        piece_type: str,
        color: ArmId,
        *,
        ghost: bool = False,
        held: bool = False,
    ) -> None:
        pygame = self.pygame
        center = self.viewport.screen(point)
        radius = self.viewport.length(16 if not ghost else 11)
        fill = (245, 241, 230) if color is ArmId.WHITE else (34, 35, 39)
        outline = (235, 197, 95) if held else ((50, 55, 65) if color is ArmId.WHITE else (220, 220, 220))
        if ghost:
            fill = tuple(max(0, c - 25) for c in fill)
        pygame.draw.circle(self.screen, fill, center, radius)
        pygame.draw.circle(self.screen, outline, center, radius, width=2)
        text_color = (18, 20, 24) if color is ArmId.WHITE else (245, 245, 245)
        symbol = PIECE_SYMBOLS.get(piece_type, piece_type)
        surface = self.piece_font.render(symbol, True, text_color)
        rect = surface.get_rect(center=center)
        self.screen.blit(surface, rect)

    def _draw_arms(self) -> None:
        pygame = self.pygame
        arm_colors = {ArmId.WHITE: (96, 167, 255), ArmId.BLACK: (255, 109, 109)}
        for arm_id, arm in self.sim.arms.items():
            base, elbow, tool = self.sim.forward_kinematics(arm_id, arm.pose)
            color = arm_colors[arm_id]
            points = [self.viewport.screen(base), self.viewport.screen(elbow), self.viewport.screen(tool)]
            pygame.draw.lines(self.screen, (18, 20, 24), False, points, width=12)
            pygame.draw.lines(self.screen, color, False, points, width=7)
            pygame.draw.circle(self.screen, color, points[1], self.viewport.length(10))
            pygame.draw.circle(
                self.screen, (245, 221, 111), self.viewport.screen(arm.tool), self.viewport.length(9)
            )
            magnet_color = (88, 255, 180) if arm.held_token_id else (145, 150, 165)
            pygame.draw.circle(
                self.screen, magnet_color, self.viewport.screen(arm.tool), self.viewport.length(5)
            )
            self._tiny("fixed height", Point(arm.tool.x_mm + 28, arm.tool.y_mm + 18), (210, 218, 230))

    def _draw_panel(self) -> None:
        pygame = self.pygame
        panel = pygame.Rect(self.viewport.width - 330, 26, 300, 230)
        pygame.draw.rect(self.screen, (16, 18, 24), panel, border_radius=14)
        pygame.draw.rect(self.screen, (72, 80, 98), panel, width=1, border_radius=14)
        lines = [
            "Dual-SCARA Chess Robot",
            f"Game: {self.sim.stats.game_number}",
            f"Ply: {self.sim.stats.plies}",
            f"Last move: {self.sim.stats.last_move}",
            f"Mode: {self.sim.stats.mode}",
            f"Transfers: {self.sim.stats.completed_transfers}",
            f"Speed: {self.sim.options.speed:0.2f}x",
            f"State: {'paused' if self.sim.paused else 'running'}",
            f"Next dead slots: {self.sim.next_dead_slot_summary()}",
            "",
            self.sim.stats.message,
        ]
        y = panel.y + 16
        for idx, line in enumerate(lines):
            font = self.font if idx == 0 else self.small
            color = (245, 247, 250) if idx == 0 else (190, 200, 215)
            for wrapped in self._wrap(line, 34):
                surface = font.render(wrapped, True, color)
                self.screen.blit(surface, (panel.x + 16, y))
                y += 22 if idx == 0 else 18

    def _rect_from_world(self, a: Point, b: Point):
        pygame = self.pygame
        x1, y1 = self.viewport.screen(a)
        x2, y2 = self.viewport.screen(b)
        return pygame.Rect(min(x1, x2), min(y1, y2), abs(x2 - x1), abs(y2 - y1))

    def _label(self, text: str, point: Point, color: tuple[int, int, int], *, dy: int = 0) -> None:
        surface = self.small.render(text, True, color)
        rect = surface.get_rect(center=(self.viewport.screen(point)[0], self.viewport.screen(point)[1] + dy))
        self.screen.blit(surface, rect)

    def _tiny(self, text: str, point: Point, color: tuple[int, int, int]) -> None:
        surface = self.small.render(text, True, color)
        rect = surface.get_rect(center=self.viewport.screen(point))
        self.screen.blit(surface, rect)

    @staticmethod
    def _wrap(text: str, width: int) -> list[str]:
        if len(text) <= width:
            return [text]
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > width and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
        return lines
