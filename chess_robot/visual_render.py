from __future__ import annotations

from dataclasses import dataclass

import chess

from .config import ArmId
from .geometry import Point
from .visual_models import PIECE_SYMBOLS, Viewport


@dataclass
class UiButton:
    action: str
    rect: object  # pygame.Rect
    label: str
    kind: str = "action"  # action | toggle | speed
    active: bool = False
    enabled: bool = True


class Palette:
    """Control-board color tokens (validated light-mode palette)."""

    SURFACE = (252, 252, 251)
    SURFACE_ALT = (245, 245, 243)
    CARD_BORDER = (223, 222, 215)
    INK = (17, 17, 17)
    INK_SECONDARY = (82, 81, 78)
    INK_MUTED = (137, 135, 129)
    GRIDLINE = (228, 227, 220)
    ACCENT = (42, 120, 214)
    ACCENT_DARK = (23, 86, 163)
    ACCENT_SOFT = (227, 238, 252)
    GOOD = (13, 140, 66)
    WARNING = (191, 122, 22)
    HEADER = (26, 43, 71)
    HEADER_ACCENT = (98, 158, 235)
    CONSOLE_BG = (26, 30, 39)
    CONSOLE_TEXT = (206, 214, 228)
    CONSOLE_MUTED = (120, 130, 148)


class PygameRenderer:
    def __init__(self, simulator, viewport: Viewport | None = None) -> None:
        import pygame

        self.pygame = pygame
        self.sim = simulator
        opts = simulator.options
        if viewport is None:
            viewport = Viewport(
                width=opts.window_width,
                height=opts.window_height,
                dashboard_width=Viewport.dashboard_width_for(opts.window_width),
            )
        self.viewport = viewport
        self.fullscreen = bool(opts.fullscreen)
        # Last windowed size so leaving fullscreen restores a normal window.
        self._windowed_size = (self.viewport.width, self.viewport.height)
        pygame.init()
        self.screen = self._create_display()
        pygame.display.set_caption("Chess Robot — Control Board")
        self.clock = pygame.time.Clock()
        self.title_font = pygame.font.SysFont("segoeui", 21, bold=True)
        self.font = pygame.font.SysFont("segoeui", 16)
        self.small = pygame.font.SysFont("segoeui", 14)
        self.section_font = pygame.font.SysFont("segoeui", 12, bold=True)
        self.mono = pygame.font.SysFont("consolas", 13)
        self.piece_font = pygame.font.SysFont("segoeuisymbol", 25)
        self._buttons: list[UiButton] = []
        self._hover_action: str | None = None
        self._panel_rect = None
        self._col_scroll = {"moves": 0, "observe": 0, "controls": 0}
        self._col_max_scroll = {"moves": 0, "observe": 0, "controls": 0}
        self._col_rects: dict[str, object] = {}

    def _create_display(self):
        """Open a resizable window by default; optional exclusive fullscreen."""
        pygame = self.pygame
        if self.fullscreen:
            # Desktop-size fullscreen; keep viewport in sync with actual pixels.
            info = pygame.display.Info()
            w, h = info.current_w or self.viewport.width, info.current_h or self.viewport.height
            self.viewport.resize(w, h)
            flags = pygame.FULLSCREEN
        else:
            flags = pygame.RESIZABLE
        return pygame.display.set_mode((self.viewport.width, self.viewport.height), flags)

    def set_fullscreen(self, enabled: bool) -> None:
        if enabled == self.fullscreen:
            return
        if enabled:
            self._windowed_size = (self.viewport.width, self.viewport.height)
            self.fullscreen = True
        else:
            self.fullscreen = False
            w, h = self._windowed_size
            self.viewport.resize(w, h)
        self.sim.options.fullscreen = self.fullscreen
        self.screen = self._create_display()
        self.sim.stats.message = "Fullscreen ON (F11)" if self.fullscreen else "Windowed mode (F11)"

    def toggle_fullscreen(self) -> None:
        self.set_fullscreen(not self.fullscreen)

    def _apply_resize(self, width: int, height: int) -> None:
        if self.fullscreen:
            return
        self.viewport.resize(width, height)
        self._windowed_size = (self.viewport.width, self.viewport.height)
        self.sim.options.window_width = self.viewport.width
        self.sim.options.window_height = self.viewport.height
        # Re-create so pygame's surface matches the resized viewport.
        self.screen = self.pygame.display.set_mode(
            (self.viewport.width, self.viewport.height), self.pygame.RESIZABLE
        )

    def run(self) -> None:
        pygame = self.pygame
        running = True
        try:
            while running:
                dt_s = self.clock.tick(self.sim.options.fps) / 1000.0
                mouse = pygame.mouse.get_pos()
                self._hover_action = self._hit_button(mouse)
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        running = False
                    elif event.type == pygame.VIDEORESIZE and not self.fullscreen:
                        self._apply_resize(event.w, event.h)
                    elif event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_q):
                            if self.fullscreen:
                                self.set_fullscreen(False)
                            else:
                                running = False
                        elif event.key == pygame.K_F11:
                            self.toggle_fullscreen()
                        elif event.key == pygame.K_SPACE:
                            self.sim.toggle_pause()
                        elif event.key == pygame.K_n:
                            self.sim.request_single_step()
                        elif event.key == pygame.K_r:
                            self.sim.reset_now()
                        elif event.key == pygame.K_s:
                            self.sim.skip_animation()
                        elif event.key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS):
                            self.sim.nudge_speed(1.25)
                        elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                            self.sim.nudge_speed(1 / 1.25)
                        elif event.key == pygame.K_l:
                            self.sim.toggle_auto_loop()
                        elif event.key == pygame.K_p:
                            self.sim.toggle_show_paths()
                        elif event.key == pygame.K_PAGEUP:
                            self._scroll_column_at(mouse, -80)
                        elif event.key == pygame.K_PAGEDOWN:
                            self._scroll_column_at(mouse, 80)
                        elif event.key == pygame.K_HOME:
                            col = self._column_at(mouse)
                            if col:
                                self._col_scroll[col] = 0
                        elif event.key == pygame.K_END:
                            col = self._column_at(mouse)
                            if col:
                                self._col_scroll[col] = self._col_max_scroll.get(col, 0)
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        action = self._hit_button(event.pos)
                        if action:
                            self._dispatch(action)
                    elif event.type == pygame.MOUSEWHEEL:
                        self._scroll_column_at(mouse, -event.y * 40)
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button in (4, 5):
                        self._scroll_column_at(mouse, -40 if event.button == 4 else 40)
                self.sim.tick(dt_s)
                self.draw()
                pygame.display.flip()
        finally:
            self.sim.close()
            pygame.quit()

    def _column_at(self, pos: tuple[int, int]) -> str | None:
        for name, rect in self._col_rects.items():
            if rect is not None and rect.collidepoint(pos):
                return name
        return None

    def _scroll_column_at(self, pos: tuple[int, int], delta: int) -> None:
        col = self._column_at(pos)
        if col is None:
            return
        max_scroll = self._col_max_scroll.get(col, 0)
        self._col_scroll[col] = max(0, min(max_scroll, self._col_scroll.get(col, 0) + delta))

    def _hit_button(self, pos: tuple[int, int]) -> str | None:
        for button in self._buttons:
            if button.enabled and button.rect.collidepoint(pos):
                return button.action
        return None

    def _dispatch(self, action: str) -> None:
        sim = self.sim
        if action == "play":
            sim.resume()
        elif action == "pause":
            sim.pause()
        elif action == "step":
            sim.request_single_step()
        elif action == "skip":
            sim.skip_animation()
        elif action == "reset":
            sim.reset_now()
        elif action == "next_game":
            sim.reset_now()
            sim.resume()
        elif action == "speed_down":
            sim.nudge_speed(1 / 1.25)
        elif action == "speed_up":
            sim.nudge_speed(1.25)
        elif action.startswith("speed_"):
            try:
                sim.set_speed(float(action.split("_", 1)[1]))
            except ValueError:
                pass
        elif action == "auto_loop":
            sim.toggle_auto_loop()
        elif action == "show_paths":
            sim.toggle_show_paths()
        elif action == "show_labels":
            sim.toggle_show_labels()
        elif action == "windowed":
            self.set_fullscreen(False)
        elif action == "fullscreen":
            self.set_fullscreen(True)

    def draw(self) -> None:
        # Light studio background
        self.screen.fill((232, 236, 242))
        self._draw_workspace()
        self._draw_storage()
        self._draw_board()
        self._draw_planned_paths()
        self._draw_pieces()
        self._draw_arms()
        self._draw_panel()

    def _draw_workspace(self) -> None:
        pygame = self.pygame
        ox, oy = self.sim.config.table_origin_x_mm, self.sim.config.table_origin_y_mm
        table_x2, table_y2 = ox + self.sim.config.table_width_mm, oy + self.sim.config.table_height_mm
        # The card must clear both arm bases (plus their circle+label), not
        # just the table -- arms may be mounted well beyond the table edge.
        pad = 45
        base_xs = [self.sim.config.arm(a).base_x_mm for a in ArmId]
        base_ys = [self.sim.config.arm(a).base_y_mm for a in ArmId]
        board_rect = self._rect_from_world(
            Point(min([ox, *base_xs]) - pad, min([oy, *base_ys]) - pad),
            Point(max([table_x2, *base_xs]) + pad, max([table_y2, *base_ys]) + pad),
        )
        pygame.draw.rect(self.screen, (245, 247, 250), board_rect, border_radius=18)
        pygame.draw.rect(self.screen, (180, 188, 200), board_rect, width=2, border_radius=18)
        for arm in ArmId:
            base = Point(self.sim.config.arm(arm).base_x_mm, self.sim.config.arm(arm).base_y_mm)
            pygame.draw.circle(self.screen, (150, 160, 180), self.viewport.screen(base), self.viewport.length(18))
            label = "White robot base" if arm is ArmId.WHITE else "Black robot base"
            self._label(label, base, (70, 80, 100), dy=-28 if arm is ArmId.WHITE else 24)

    def _draw_board(self) -> None:
        pygame = self.pygame
        layout = self.sim.layout
        colors = ((245, 232, 205), (125, 158, 118))
        size = self.sim.config.square_size_mm
        ox, oy = self.sim.config.table_origin_x_mm, self.sim.config.table_origin_y_mm
        chess_start_col = layout.chess_start_col
        chess_end_col = layout.chess_end_col
        for row_from_bottom in range(self.sim.config.table_rows):
            for table_col in range(self.sim.config.table_columns):
                world = Point(ox + table_col * size, oy + row_from_bottom * size)
                rect = self._rect_from_world(world, Point(world.x_mm + size, world.y_mm + size))
                cell_name = layout.cell_label(table_col, row_from_bottom)
                if chess_start_col <= table_col < chess_end_col:
                    chess_file = table_col - chess_start_col
                    pygame.draw.rect(self.screen, colors[(row_from_bottom + chess_file) % 2], rect)
                else:
                    pygame.draw.rect(self.screen, (220, 224, 232), rect)
                    pygame.draw.rect(self.screen, (190, 196, 208), rect, width=1)
                pygame.draw.circle(
                    self.screen,
                    (160, 168, 180),
                    self.viewport.screen(Point(world.x_mm + size / 2, world.y_mm + size / 2)),
                    self.viewport.length(3),
                )
                if self.sim.options.show_cell_labels:
                    label_color = (
                        (80, 90, 105)
                        if chess_start_col <= table_col < chess_end_col
                        else (130, 110, 90)
                    )
                    self._tiny(
                        cell_name,
                        Point(world.x_mm + size / 2, world.y_mm + size - 10),
                        label_color,
                    )
        table_outline = self._rect_from_world(
            Point(ox, oy),
            Point(ox + self.sim.config.table_width_mm, oy + self.sim.config.table_height_mm),
        )
        chess_outline = self._rect_from_world(
            Point(ox + self.sim.config.board_origin_x_mm, oy + self.sim.config.board_origin_y_mm),
            Point(
                ox + self.sim.config.board_origin_x_mm + self.sim.config.board_size_mm,
                oy + self.sim.config.board_origin_y_mm + self.sim.config.board_size_mm,
            ),
        )
        pygame.draw.rect(self.screen, (60, 68, 82), table_outline, width=3)
        pygame.draw.rect(self.screen, (40, 48, 60), chess_outline, width=3)
        # Axis legends: C1…C12 left→right, R1…R8 bottom→top (matches chess ranks).
        for table_col in range(self.sim.config.table_columns):
            self._tiny(
                layout.column_label(table_col),
                Point(ox + (table_col + 0.5) * size, oy - 16),
                (70, 80, 95),
            )
        for row_from_bottom in range(self.sim.config.table_rows):
            y = oy + (row_from_bottom + 0.5) * size
            self._tiny(layout.row_label(row_from_bottom), Point(ox - 22, y), (70, 80, 95))
        # Chess file/rank strip along the play area for quick orientation.
        for table_col in range(chess_start_col, chess_end_col):
            file_letter = chr(ord("a") + (table_col - chess_start_col))
            self._tiny(
                file_letter,
                Point(ox + (table_col + 0.5) * size, oy + self.sim.config.table_height_mm + 16),
                (80, 90, 110),
            )
        for row_from_bottom in range(self.sim.config.board_squares):
            self._tiny(
                str(row_from_bottom + 1),
                Point(
                    ox + self.sim.config.board_origin_x_mm + self.sim.config.board_size_mm + 14,
                    oy + (row_from_bottom + 0.5) * size,
                ),
                (80, 90, 110),
            )

    def _draw_storage(self) -> None:
        pygame = self.pygame
        ox, oy = self.sim.config.table_origin_x_mm, self.sim.config.table_origin_y_mm
        for arm in ArmId:
            for index in range(16):
                p = self.sim.layout.dead_slot(arm, index)
                pygame.draw.circle(
                    self.screen, (160, 120, 110), self.viewport.screen(p), self.viewport.length(14), width=2
                )
            rack_x = ox + 50 if arm is ArmId.WHITE else ox + 550
            self._label(
                f"{arm.value.title()} rack W1–W16" if arm is ArmId.WHITE else "Black rack B1–B16",
                Point(rack_x, oy + 430),
                (90, 100, 120),
                dy=-8,
            )

    def _draw_planned_paths(self) -> None:
        if not self.sim.options.show_paths:
            return
        pygame = self.pygame
        for path in self.sim.current_plan_paths[:6]:
            if len(path.points) < 2:
                continue
            screen_points = [self.viewport.screen(point) for point in path.points]
            pygame.draw.lines(self.screen, (200, 140, 20), False, screen_points, width=2)
            for point in screen_points[1:-1]:
                pygame.draw.circle(self.screen, (200, 140, 20), point, self.viewport.length(4))

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
            base, elbow, wrist, tool = self.sim.forward_kinematics(arm_id, arm.pose)
            color = arm_colors[arm_id]
            points = [
                self.viewport.screen(base),
                self.viewport.screen(elbow),
                self.viewport.screen(wrist),
                self.viewport.screen(tool),
            ]
            pygame.draw.lines(self.screen, (18, 20, 24), False, points, width=12)
            pygame.draw.lines(self.screen, color, False, points, width=7)
            joint_r = self.viewport.length(9)
            pygame.draw.circle(self.screen, color, points[1], joint_r)
            pygame.draw.circle(self.screen, color, points[2], joint_r)
            pygame.draw.circle(
                self.screen, (245, 221, 111), self.viewport.screen(arm.tool), self.viewport.length(9)
            )
            magnet_color = (88, 255, 180) if arm.magnet_on else (145, 150, 165)
            pygame.draw.circle(
                self.screen, magnet_color, self.viewport.screen(arm.tool), self.viewport.length(5)
            )

    def _draw_panel(self) -> None:
        """Right-side panel: header + three columns (moves | observe | controls)."""
        pygame = self.pygame
        stats = self.sim.stats
        opts = self.sim.options
        pad = 12
        width = self.viewport.dashboard_width - 12
        x0 = self.viewport.width - self.viewport.dashboard_width + 4
        y0 = 10
        height = self.viewport.height - 20
        panel = pygame.Rect(x0, y0, width, height)
        self._panel_rect = panel
        pal = Palette
        pygame.draw.rect(self.screen, pal.SURFACE, panel, border_radius=14)
        pygame.draw.rect(self.screen, pal.CARD_BORDER, panel, width=1, border_radius=14)

        # —— Fixed header (full width) ——
        header_h = 68
        header = pygame.Rect(panel.x + 1, panel.y + 1, panel.width - 2, header_h)
        pygame.draw.rect(self.screen, pal.HEADER, header, border_radius=13)
        seam = pygame.Rect(header.x + 12, header.bottom - 2, header.width - 24, 2)
        pygame.draw.rect(self.screen, pal.HEADER_ACCENT, seam, border_radius=1)
        self._blit_text("DUAL-SCARA", panel.x + pad, panel.y + 10, self.small, pal.HEADER_ACCENT)
        self._blit_text("CONTROL BOARD", panel.x + pad, panel.y + 24, self.title_font, (255, 255, 255))
        running = not self.sim.paused
        move_no = stats.plies // 2 + 1
        side = "WHITE" if self.sim.board.turn else "BLACK"
        self._blit_text(
            f"GAME {stats.game_number}  ·  MOVE {move_no}  ·  {side}  ·  {opts.speed:0.2f}×",
            panel.x + pad,
            panel.y + 48,
            self.small,
            (198, 212, 232),
        )
        badge = "RUNNING" if running else "PAUSED"
        badge_color = pal.GOOD if running else pal.WARNING
        badge_surface = self.small.render(badge, True, (255, 255, 255))
        badge_rect = pygame.Rect(
            panel.right - pad - badge_surface.get_width() - 20,
            panel.y + 16,
            badge_surface.get_width() + 20,
            badge_surface.get_height() + 6,
        )
        pygame.draw.rect(self.screen, badge_color, badge_rect, border_radius=8)
        pygame.draw.circle(self.screen, (255, 255, 255), (badge_rect.x + 10, badge_rect.centery), 3)
        self.screen.blit(badge_surface, (badge_rect.x + 18, badge_rect.y + 3))

        # —— Three columns under the header ——
        body = pygame.Rect(panel.x + 6, panel.y + header_h + 6, panel.width - 12, panel.height - header_h - 12)
        gap = 10
        col_w = (body.width - gap * 2) // 3
        moves_rect = pygame.Rect(body.x, body.y, col_w, body.height)
        observe_rect = pygame.Rect(body.x + col_w + gap, body.y, col_w, body.height)
        controls_rect = pygame.Rect(body.x + 2 * (col_w + gap), body.y, body.width - 2 * (col_w + gap), body.height)
        self._col_rects = {"moves": moves_rect, "observe": observe_rect, "controls": controls_rect}

        # Column backgrounds
        for rect in (moves_rect, observe_rect, controls_rect):
            pygame.draw.rect(self.screen, pal.SURFACE_ALT, rect, border_radius=10)
            pygame.draw.rect(self.screen, pal.CARD_BORDER, rect, width=1, border_radius=10)

        self._buttons = []
        self._draw_moves_column(moves_rect, stats)
        self._draw_observe_column(observe_rect, stats, opts)
        self._draw_controls_column(controls_rect, opts, running)

    def _begin_column(self, rect, name: str) -> tuple[int, int, int, int, object]:
        """Clip to column and return (x, y, inner_w, content_top, prev_clip)."""
        pad = 10
        clip = rect.inflate(-4, -4)
        prev_clip = self.screen.get_clip()
        self.screen.set_clip(clip)
        x = rect.x + pad
        inner_w = rect.width - pad * 2 - 8
        content_top = rect.y + pad
        y = content_top - self._col_scroll.get(name, 0)
        return x, y, inner_w, content_top, prev_clip

    def _end_column(self, rect, name: str, y: int, content_top: int, prev_clip, pad: int = 10) -> None:
        pygame = self.pygame
        content_height = (y + self._col_scroll.get(name, 0)) - content_top + pad
        view_height = rect.height - 2 * pad
        max_scroll = max(0, content_height - view_height)
        self._col_max_scroll[name] = max_scroll
        self._col_scroll[name] = max(0, min(max_scroll, self._col_scroll.get(name, 0)))
        self.screen.set_clip(prev_clip)
        if max_scroll > 0:
            track = pygame.Rect(rect.right - 8, rect.y + 8, 4, rect.height - 16)
            pygame.draw.rect(self.screen, Palette.GRIDLINE, track, border_radius=2)
            thumb_h = max(24, int(track.height * view_height / max(content_height, 1)))
            thumb_y = track.y + int(
                (track.height - thumb_h) * (self._col_scroll[name] / max_scroll)
            )
            pygame.draw.rect(
                self.screen,
                Palette.ACCENT,
                pygame.Rect(track.x, thumb_y, track.width, thumb_h),
                border_radius=2,
            )

    def _draw_moves_column(self, rect, stats) -> None:
        x, y, inner_w, content_top, prev = self._begin_column(rect, "moves")
        y = self._section_title(x, y, "MOVES")
        y = self._draw_move_history(x, y, inner_w, stats.moves_uci or [])
        self._end_column(rect, "moves", y, content_top, prev)

    def _draw_observe_column(self, rect, stats, opts) -> None:
        x, y, inner_w, content_top, prev = self._begin_column(rect, "observe")
        running = not self.sim.paused
        magnet_on = any(arm.magnet_on for arm in self.sim.arms.values())
        progress = 0.0
        if stats.plan_transfers_total:
            progress = stats.plan_transfers_done / max(1, stats.plan_transfers_total)

        y = self._section_title(x, y, "NOW RUNNING")
        y = self._operation_card(
            x,
            y,
            inner_w,
            arm=stats.active_arm,
            step=stats.active_step_label,
            magnet_on=magnet_on,
            plan=f"{stats.plan_transfers_done}/{stats.plan_transfers_total}",
            progress=progress,
        )
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "GAME")
        move_no = stats.plies // 2 + 1
        side = "White" if self.sim.board.turn else "Black"
        y = self._kv_card(
            x,
            y,
            inner_w,
            [
                ("Match", f"#{stats.game_number}"),
                ("Move", f"{move_no} ({side})"),
                ("Ply / last", f"{stats.plies} · {stats.last_move_san}"),
                ("Mode", stats.mode),
                ("Result", stats.last_result or "—"),
                ("State", "Running" if running else "Paused"),
            ],
        )
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "ENGINES")
        if opts.use_engine:
            y = self._player_card(x, y, inner_w, "White", opts.white_elo, opts.white_skill, opts.move_time_s)
            y = self._player_card(x, y, inner_w, "Black", opts.black_elo, opts.black_skill, opts.move_time_s)
            fair = opts.white_elo == opts.black_elo and opts.white_skill == opts.black_skill
            y = self._kv_row(x, y, inner_w, "Match", "Fair" if fair else "Handicap")
        else:
            y = self._kv_row(x, y, inner_w, "Mode", "Random legal moves")
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "MOTION")
        y = self._kv_card(
            x,
            y,
            inner_w,
            [
                ("Transfers", str(stats.completed_transfers)),
                ("Rack", self.sim.next_dead_slot_summary()),
                ("Path skips", str(stats.path_skips)),
            ],
        )
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "ARM STATE")
        arm_colors = {ArmId.WHITE: (96, 167, 255), ArmId.BLACK: (255, 109, 109)}
        for arm_id in ArmId:
            y = self._arm_state_card(x, y, inner_w, arm_id, self.sim.arms[arm_id], arm_colors[arm_id])
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "STATUS")
        y = self._status_card(x, y, inner_w, stats.message)
        self._end_column(rect, "observe", y, content_top, prev)

    def _draw_controls_column(self, rect, opts, running: bool) -> None:
        x, y, inner_w, content_top, prev = self._begin_column(rect, "controls")
        # Buttons use this rect for partial-visibility clipping.
        self._panel_content_rect = rect.inflate(-4, -4)

        y = self._section_title(x, y, "TRANSPORT")
        y = self._button_row(
            x, y, inner_w, [("play", "Play", not running), ("pause", "Pause", running)], height=32
        )
        y = self._button_row(x, y, inner_w, [("step", "Step", True), ("skip", "Skip anim", True)], height=32)
        y = self._button_row(
            x, y, inner_w, [("reset", "Reset", True), ("next_game", "Next game", True)], height=32
        )
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "SPEED")
        y = self._button_row(
            x,
            y,
            inner_w,
            [
                ("speed_down", "−", True),
                ("speed_0.5", "0.5×", abs(opts.speed - 0.5) < 0.05),
                ("speed_1.0", "1×", abs(opts.speed - 1.0) < 0.05),
                ("speed_2.0", "2×", abs(opts.speed - 2.0) < 0.05),
            ],
            height=28,
        )
        y = self._button_row(
            x,
            y,
            inner_w,
            [
                ("speed_5.0", "5×", abs(opts.speed - 5.0) < 0.05),
                ("speed_10.0", "10×", abs(opts.speed - 10.0) < 0.05),
                ("speed_up", "+", True),
            ],
            height=28,
        )
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "WINDOW")
        y = self._button_row(
            x,
            y,
            inner_w,
            [
                ("windowed", "Windowed", not self.fullscreen),
                ("fullscreen", "Fullscreen", self.fullscreen),
            ],
            height=30,
        )
        self._blit_text(
            f"{self.viewport.width}×{self.viewport.height}  ·  F11 toggle",
            x,
            y,
            self.mono,
            Palette.INK_MUTED,
        )
        y += 18
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "MODES")
        for action, label, flag in (
            ("auto_loop", "Auto-loop", opts.auto_loop),
            ("show_paths", "Paths", opts.show_paths),
            ("show_labels", "Labels", opts.show_cell_labels),
        ):
            y = self._button_row(x, y, inner_w, [(action, label, flag)], height=30)
        y = self._section_rule(x, y, inner_w)

        y = self._section_title(x, y, "KEYS")
        for line in (
            "Space play/pause",
            "N step · S skip",
            "R reset · L auto-loop",
            "P paths · F11 fullscreen",
            "Esc windowed / quit",
            "+/- speed",
            "Drag corner to resize",
            "Wheel scrolls column",
        ):
            self._blit_text(line, x, y, self.mono, Palette.INK_MUTED)
            y += 15
        self._end_column(rect, "controls", y, content_top, prev)
        self._panel_content_rect = None

    def _button_row(
        self,
        x: int,
        y: int,
        width: int,
        items: list[tuple[str, str, bool]],
        height: int = 32,
    ) -> int:
        """Draw a row of equal-width buttons. items: (action, label, active_or_enabled hint)."""
        pygame = self.pygame
        gap = 5
        n = max(1, len(items))
        btn_w = (width - gap * (n - 1)) // n
        clip_rect = getattr(self, "_panel_content_rect", None)
        for i, (action, label, flag) in enumerate(items):
            bx = x + i * (btn_w + gap)
            rect = pygame.Rect(bx, y, btn_w, height)
            hover = self._hover_action == action
            if action in {"auto_loop", "show_paths", "show_labels"} or action.startswith("speed_"):
                active = flag
            elif action in {"play", "pause"}:
                active = flag
            else:
                active = False
            visible_rect = rect
            if clip_rect is not None:
                visible_rect = rect.clip(clip_rect)
            if visible_rect.width and visible_rect.height:
                self._draw_button(rect, label, active=active, hover=hover, enabled=True)
                self._buttons.append(UiButton(action, visible_rect, label, active=active, enabled=True))
        return y + height + 5

    def _draw_button(
        self,
        rect,
        label: str,
        *,
        active: bool,
        hover: bool,
        enabled: bool,
    ) -> None:
        pygame = self.pygame
        pal = Palette
        if not enabled:
            fill, border, text = pal.SURFACE_ALT, pal.CARD_BORDER, pal.INK_MUTED
        elif active:
            fill, border, text = pal.ACCENT, pal.ACCENT_DARK, (255, 255, 255)
        elif hover:
            fill, border, text = pal.ACCENT_SOFT, pal.ACCENT, pal.ACCENT_DARK
        else:
            fill, border, text = pal.SURFACE, pal.CARD_BORDER, pal.INK_SECONDARY
        pygame.draw.rect(self.screen, fill, rect, border_radius=8)
        pygame.draw.rect(self.screen, border, rect, width=1, border_radius=8)
        if active:
            # A thin lighter seam along the top edge reads as a soft gloss,
            # giving the pressed/active state a touch of depth.
            highlight = pygame.Rect(rect.x + 3, rect.y + 1, rect.width - 6, 2)
            pygame.draw.rect(self.screen, (110, 168, 235), highlight, border_radius=1)
        surface = self.small.render(label, True, text)
        self.screen.blit(
            surface,
            (rect.centerx - surface.get_width() // 2, rect.centery - surface.get_height() // 2),
        )

    def _section_title(self, x: int, y: int, title: str) -> int:
        pygame = self.pygame
        tick = pygame.Rect(x, y + 2, 3, 10)
        pygame.draw.rect(self.screen, Palette.ACCENT, tick, border_radius=1)
        self._blit_text(title, x + 9, y, self.section_font, Palette.ACCENT_DARK)
        return y + 18

    def _section_rule(self, x: int, y: int, width: int) -> int:
        pygame = self.pygame
        pygame.draw.line(self.screen, Palette.GRIDLINE, (x, y), (x + width, y), 1)
        return y + 10

    def _fit_text(self, text: str, font, max_width: int) -> object:
        """Render text, truncating with an ellipsis if wider than max_width."""
        surface = font.render(str(text), True, Palette.INK)
        if surface.get_width() <= max_width:
            return surface
        raw = str(text)
        while raw and font.size(raw + "…")[0] > max_width:
            raw = raw[:-1]
        return font.render((raw + "…") if raw else "…", True, Palette.INK)

    def _kv_row(self, x: int, y: int, width: int, label: str, value: str) -> int:
        """Label on its own line, value on the next — safe for narrow columns."""
        line_h = 17
        self._blit_text(label, x, y, self.small, Palette.INK_MUTED)
        value_surface = self._fit_text(str(value), self.small, width)
        # Tint value surface to INK (fit_text already uses INK)
        self.screen.blit(value_surface, (x, y + line_h))
        return y + line_h * 2 + 4

    def _kv_card(self, x: int, y: int, width: int, rows: list[tuple[str, str]]) -> int:
        """A light card grouping label/value rows with room for two-line pairs."""
        pygame = self.pygame
        pad = 10
        # Each row is label + value + gap ≈ 38px
        row_h = 38
        height = pad * 2 + row_h * len(rows)
        rect = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, Palette.SURFACE_ALT, rect, border_radius=8)
        pygame.draw.rect(self.screen, Palette.CARD_BORDER, rect, width=1, border_radius=8)
        ty = y + pad
        for label, value in rows:
            ty = self._kv_row(x + pad, ty, width - pad * 2, label, value)
        return y + height + 8

    def _status_card(self, x: int, y: int, width: int, message: str) -> int:
        """The operator's current status message, flagged with an accent rail."""
        pygame = self.pygame
        pad = 10
        # Wrap by pixel width for the actual column, not a fixed char count.
        max_chars = max(12, width // 7)
        lines = self._wrap(message or "—", max_chars)[:8] or ["—"]
        line_h = 17
        height = pad * 2 + line_h * len(lines)
        rect = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, Palette.SURFACE_ALT, rect, border_radius=8)
        pygame.draw.rect(self.screen, Palette.CARD_BORDER, rect, width=1, border_radius=8)
        rail = pygame.Rect(rect.x, rect.y, 3, rect.height)
        pygame.draw.rect(self.screen, Palette.ACCENT, rail, border_radius=1)
        ty = y + pad
        for line in lines:
            self._blit_text(line, x + pad + 4, ty, self.small, Palette.INK_SECONDARY)
            ty += line_h
        return y + height + 8

    def _arm_state_card(
        self, x: int, y: int, width: int, arm_id: ArmId, arm, color: tuple[int, int, int]
    ) -> int:
        """Joint angles + end-effector position for one SCARA arm."""
        pygame = self.pygame
        pad = 10
        pose = arm.pose
        joint1 = f"{pose.shoulder_deg:0.1f}°" if pose else "—"
        joint2 = f"{pose.elbow_deg:0.1f}°" if pose else "—"
        joint3 = f"{pose.wrist_deg:0.1f}°" if pose else "—"
        rows = [
            ("Joint 1 (shoulder)", joint1),
            ("Joint 2 (elbow)", joint2),
            ("Joint 3 (wrist)", joint3),
            ("End effector X", f"{arm.tool.x_mm:0.0f} mm"),
            ("End effector Y", f"{arm.tool.y_mm:0.0f} mm"),
        ]
        title_h = 22
        row_h = 38
        height = pad + title_h + row_h * len(rows) + pad
        rect = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, Palette.SURFACE_ALT, rect, border_radius=8)
        pygame.draw.rect(self.screen, Palette.CARD_BORDER, rect, width=1, border_radius=8)
        pygame.draw.circle(self.screen, color, (x + pad + 5, y + pad + 8), 5)
        self._blit_text(f"{arm_id.value.title()} Arm", x + pad + 16, y + pad, self.font, Palette.INK)
        ty = y + pad + title_h
        for label, value in rows:
            ty = self._kv_row(x + pad, ty, width - pad * 2, label, value)
        return y + height + 8

    def _player_card(
        self, x: int, y: int, width: int, side: str, elo: int, skill: int, think_s: float
    ) -> int:
        """Engine profile card with stacked lines (no horizontal crowding)."""
        pygame = self.pygame
        pad = 10
        height = pad + 18 + 17 * 3 + pad
        rect = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, Palette.SURFACE_ALT, rect, border_radius=8)
        pygame.draw.rect(self.screen, Palette.CARD_BORDER, rect, width=1, border_radius=8)
        accent = (245, 245, 243) if side == "White" else (44, 48, 58)
        outline = (185, 184, 177) if side == "White" else (44, 48, 58)
        pygame.draw.circle(self.screen, accent, (x + pad + 5, y + pad + 8), 5)
        pygame.draw.circle(self.screen, outline, (x + pad + 5, y + pad + 8), 5, width=1)
        self._blit_text(side, x + pad + 16, y + pad, self.font, Palette.INK)
        ty = y + pad + 20
        for label, value in (
            ("Elo", str(elo)),
            ("Skill", str(skill)),
            ("Think time", f"{think_s:0.1f} s"),
        ):
            self._blit_text(label, x + pad, ty, self.small, Palette.INK_MUTED)
            val = self._fit_text(value, self.small, width - pad * 2 - 80)
            # recolor: fit_text uses INK already
            self.screen.blit(val, (x + width - pad - val.get_width(), ty))
            ty += 17
        return y + height + 8

    def _operation_card(
        self,
        x: int,
        y: int,
        width: int,
        *,
        arm: str,
        step: str,
        magnet_on: bool,
        plan: str,
        progress: float,
    ) -> int:
        """Current execution card — stacked rows, no overlapping side-by-side text."""
        pygame = self.pygame
        pal = Palette
        pad = 10
        line_h = 20
        # title + 4 data rows + gap + progress + bottom pad
        height = pad + 18 + line_h * 4 + 12 + 10 + pad
        rect = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, pal.ACCENT_SOFT, rect, border_radius=10)
        pygame.draw.rect(self.screen, pal.ACCENT, rect, width=1, border_radius=10)

        ty = y + pad
        self._blit_text("CURRENT EXECUTION", x + pad, ty, self.small, pal.ACCENT_DARK)
        ty += 20

        arm_text = arm if arm and arm != "—" else "WAITING"
        step_text = step if step else "idle"
        rows = [
            ("Arm", arm_text.upper()),
            ("Step", step_text),
            ("Plan", str(plan)),
            ("Magnet", "ON (carrying)" if magnet_on else "OFF"),
        ]
        for label, value in rows:
            self._blit_text(label, x + pad, ty, self.small, pal.INK_MUTED)
            max_w = max(40, width - pad * 2 - 72)
            surface = self._fit_text(value, self.small, max_w)
            self.screen.blit(surface, (x + width - pad - surface.get_width(), ty))
            if label == "Magnet":
                magnet_color = pal.GOOD if magnet_on else pal.INK_MUTED
                # Dot just left of the value
                pygame.draw.circle(
                    self.screen,
                    magnet_color,
                    (x + width - pad - surface.get_width() - 10, ty + 7),
                    4,
                )
            ty += line_h

        ty += 4
        self._progress_bar(x + pad, ty, width - pad * 2, progress)
        return y + height + 8

    def _progress_bar(self, x: int, y: int, width: int, fraction: float) -> int:
        pygame = self.pygame
        h = 8
        pygame.draw.rect(self.screen, (216, 227, 244), pygame.Rect(x, y, width, h), border_radius=4)
        fill = max(0, min(1.0, fraction))
        if fill > 0:
            pygame.draw.rect(
                self.screen,
                Palette.ACCENT,
                pygame.Rect(x, y, max(4, int(width * fill)), h),
                border_radius=4,
            )
        return y + h + 4

    def _draw_badge(self, x: int, y: int, text: str, color: tuple[int, int, int]) -> None:
        pygame = self.pygame
        surface = self.small.render(text, True, (255, 255, 255))
        rect = pygame.Rect(x, y, surface.get_width() + 14, surface.get_height() + 6)
        pygame.draw.rect(self.screen, color, rect, border_radius=8)
        self.screen.blit(surface, (x + 7, y + 3))

    def _draw_move_history(self, x: int, y: int, width: int, moves: list[str]) -> int:
        """Draw every played ply as ``N. Side: A1 to B2``."""
        pygame = self.pygame
        pad = 10
        line_height = 20
        line_count = max(1, len(moves))
        height = pad * 2 + line_height * line_count
        rect = pygame.Rect(x, y, width, height)
        pygame.draw.rect(self.screen, Palette.CONSOLE_BG, rect, border_radius=8)
        pygame.draw.rect(self.screen, Palette.GRIDLINE, rect, width=1, border_radius=8)
        ty = y + pad
        if not moves:
            self._blit_text("No moves yet", x + pad, ty, self.small, Palette.CONSOLE_MUTED)
            return y + height + 8
        max_w = width - pad * 2
        for index, uci in enumerate(moves, start=1):
            line = self._format_move_history_line(index, uci)
            surface = self.mono.render(line, True, Palette.CONSOLE_TEXT)
            if surface.get_width() > max_w:
                raw = line
                while raw and self.mono.size(raw + "…")[0] > max_w:
                    raw = raw[:-1]
                surface = self.mono.render(raw + "…", True, Palette.CONSOLE_TEXT)
            self.screen.blit(surface, (x + pad, ty))
            ty += line_height
        return y + height + 8

    @staticmethod
    def _format_move_history_line(index: int, uci: str) -> str:
        side = "White" if index % 2 else "Black"
        source = uci[:2].upper() if len(uci) >= 2 else "?"
        destination = uci[2:4].upper() if len(uci) >= 4 else "?"
        return f"{index}. {side}: {source} to {destination}"

    def _blit_text(self, text: str, x: int, y: int, font, color: tuple[int, int, int]) -> None:
        self.screen.blit(font.render(text, True, color), (x, y))

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
