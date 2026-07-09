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


class PygameRenderer:
    def __init__(self, simulator, viewport: Viewport | None = None) -> None:
        import pygame

        self.pygame = pygame
        self.sim = simulator
        self.viewport = viewport or Viewport()
        pygame.init()
        self.screen = pygame.display.set_mode((self.viewport.width, self.viewport.height))
        pygame.display.set_caption("Dual-SCARA Chess Robot — Control Board")
        self.clock = pygame.time.Clock()
        self.title_font = pygame.font.SysFont("segoeui", 20, bold=True)
        self.font = pygame.font.SysFont("segoeui", 16)
        self.small = pygame.font.SysFont("segoeui", 13)
        self.mono = pygame.font.SysFont("consolas", 13)
        self.piece_font = pygame.font.SysFont("segoeuisymbol", 25)
        self._buttons: list[UiButton] = []
        self._hover_action: str | None = None

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
                    elif event.type == pygame.KEYDOWN:
                        if event.key in (pygame.K_ESCAPE, pygame.K_q):
                            running = False
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
                    elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                        action = self._hit_button(event.pos)
                        if action:
                            self._dispatch(action)
                self.sim.tick(dt_s)
                self.draw()
                pygame.display.flip()
        finally:
            self.sim.close()
            pygame.quit()

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
        board_rect = self._rect_from_world(Point(-45, -155), Point(645, 555))
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
            Point(0, 0), Point(self.sim.config.table_width_mm, self.sim.config.table_height_mm)
        )
        chess_outline = self._rect_from_world(
            Point(self.sim.config.board_origin_x_mm, 0),
            Point(
                self.sim.config.board_origin_x_mm + self.sim.config.board_size_mm,
                self.sim.config.board_size_mm,
            ),
        )
        pygame.draw.rect(self.screen, (60, 68, 82), table_outline, width=3)
        pygame.draw.rect(self.screen, (40, 48, 60), chess_outline, width=3)
        # Axis legends: C1…C12 left→right, R1…R8 bottom→top (matches chess ranks).
        for table_col in range(self.sim.config.table_columns):
            self._tiny(
                layout.column_label(table_col),
                Point((table_col + 0.5) * size, -16),
                (70, 80, 95),
            )
        for row_from_bottom in range(self.sim.config.table_rows):
            y = (row_from_bottom + 0.5) * size
            self._tiny(layout.row_label(row_from_bottom), Point(-22, y), (70, 80, 95))
        # Chess file/rank strip along the play area for quick orientation.
        for table_col in range(chess_start_col, chess_end_col):
            file_letter = chr(ord("a") + (table_col - chess_start_col))
            self._tiny(
                file_letter,
                Point((table_col + 0.5) * size, self.sim.config.table_height_mm + 16),
                (80, 90, 110),
            )
        for row_from_bottom in range(self.sim.config.board_squares):
            self._tiny(
                str(row_from_bottom + 1),
                Point(
                    self.sim.config.board_origin_x_mm + self.sim.config.board_size_mm + 14,
                    (row_from_bottom + 0.5) * size,
                ),
                (80, 90, 110),
            )

    def _draw_storage(self) -> None:
        pygame = self.pygame
        for arm in ArmId:
            for index in range(16):
                p = self.sim.layout.dead_slot(arm, index)
                pygame.draw.circle(
                    self.screen, (160, 120, 110), self.viewport.screen(p), self.viewport.length(14), width=2
                )
            rack_x = 50 if arm is ArmId.WHITE else 550
            self._label(
                f"{arm.value.title()} rack W1–W16" if arm is ArmId.WHITE else "Black rack B1–B16",
                Point(rack_x, 430),
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
            if self.sim.options.show_arm_labels:
                self._tiny("fixed height", Point(arm.tool.x_mm + 28, arm.tool.y_mm + 18), (70, 80, 100))

    def _draw_panel(self) -> None:
        """Right-side control board: status + clickable operator controls."""
        pygame = self.pygame
        stats = self.sim.stats
        opts = self.sim.options
        pad = 12
        width = self.viewport.dashboard_width - 16
        x0 = self.viewport.width - self.viewport.dashboard_width + 6
        y0 = 12
        height = self.viewport.height - 24
        panel = pygame.Rect(x0, y0, width, height)
        pygame.draw.rect(self.screen, (252, 253, 255), panel, border_radius=14)
        pygame.draw.rect(self.screen, (180, 188, 204), panel, width=1, border_radius=14)

        self._buttons = []
        y = panel.y + pad
        x = panel.x + pad
        inner_w = panel.width - pad * 2

        # —— Header ——
        self._blit_text("CONTROL BOARD", x, y, self.title_font, (30, 36, 48))
        y += 24
        running = not self.sim.paused
        badge = "RUNNING" if running else "PAUSED"
        badge_color = (56, 170, 100) if running else (220, 160, 50)
        self._draw_badge(x, y, badge, badge_color)
        self._blit_text(f"{opts.speed:0.2f}×", x + 100, y + 2, self.font, (40, 48, 62))
        y += 30
        y = self._section_rule(x, y, inner_w)

        # —— Transport controls ——
        y = self._section_title(x, y, "TRANSPORT")
        row1 = [
            ("play", "Play", not running),
            ("pause", "Pause", running),
            ("step", "Step", True),
        ]
        y = self._button_row(x, y, inner_w, row1)
        row2 = [
            ("skip", "Skip anim", True),
            ("reset", "Reset", True),
            ("next_game", "Next game", True),
        ]
        y = self._button_row(x, y, inner_w, row2)
        y += 4
        y = self._section_rule(x, y, inner_w)

        # —— Speed ——
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
                ("speed_4.0", "4×", abs(opts.speed - 4.0) < 0.05),
                ("speed_up", "+", True),
            ],
            height=28,
        )
        y += 4
        y = self._section_rule(x, y, inner_w)

        # —— Modes ——
        y = self._section_title(x, y, "MODES")
        y = self._button_row(
            x,
            y,
            inner_w,
            [
                ("auto_loop", "Auto-loop", opts.auto_loop),
                ("show_paths", "Paths", opts.show_paths),
                ("show_labels", "Labels", opts.show_cell_labels),
            ],
        )
        y += 4
        y = self._section_rule(x, y, inner_w)

        # —— Game telemetry ——
        y = self._section_title(x, y, "GAME")
        move_no = stats.plies // 2 + 1
        side = "White" if self.sim.board.turn else "Black"
        for label, value in (
            ("Match", f"#{stats.game_number}"),
            ("Move", f"{move_no}  ({side})"),
            ("Ply", str(stats.plies)),
            ("Last", f"{stats.last_move_san}"),
            ("UCI", stats.last_move),
            ("Mode", stats.mode),
            ("Result", stats.last_result or "—"),
        ):
            y = self._kv_row(x, y, inner_w, label, value)
        y += 4
        y = self._section_rule(x, y, inner_w)

        # —— Players ——
        y = self._section_title(x, y, "ENGINES")
        if opts.use_engine:
            y = self._player_card(x, y, inner_w, "White", opts.white_elo, opts.white_skill, opts.move_time_s)
            y = self._player_card(x, y, inner_w, "Black", opts.black_elo, opts.black_skill, opts.move_time_s)
            fair = opts.white_elo == opts.black_elo and opts.white_skill == opts.black_skill
            y = self._kv_row(x, y, inner_w, "Match", "Fair" if fair else "Handicap")
        else:
            y = self._kv_row(x, y, inner_w, "Mode", "Random legal moves")
        y += 2
        y = self._section_rule(x, y, inner_w)

        # —— Motion ——
        y = self._section_title(x, y, "MOTION")
        held = next((a.held_token_id for a in self.sim.arms.values() if a.held_token_id), None)
        progress = 0.0
        if stats.plan_transfers_total:
            progress = stats.plan_transfers_done / max(1, stats.plan_transfers_total)
        for label, value in (
            ("Arm", stats.active_arm),
            ("Step", stats.active_step_label[:26]),
            ("Magnet", "ON" if held else "off"),
            ("Transfers", str(stats.completed_transfers)),
            ("Plan", f"{stats.plan_transfers_done}/{stats.plan_transfers_total}"),
            ("Rack", self.sim.next_dead_slot_summary()),
            ("Skips", str(stats.path_skips)),
        ):
            y = self._kv_row(x, y, inner_w, label, value)
        y = self._progress_bar(x, y + 2, inner_w, progress)
        y += 6
        y = self._section_rule(x, y, inner_w)

        # —— Moves + status ——
        y = self._section_title(x, y, "MOVES")
        y = self._draw_move_list(x, y, inner_w, stats.moves_san or [], max_lines=6)
        y += 4
        y = self._section_rule(x, y, inner_w)
        y = self._section_title(x, y, "STATUS")
        for line in self._wrap(stats.message, 34)[:4]:
            self._blit_text(line, x, y, self.small, (50, 58, 72))
            y += 15

        # —— Keyboard legend ——
        legend_y = panel.bottom - 72
        if legend_y > y + 8:
            y = self._section_rule(x, legend_y, inner_w)
            y = self._section_title(x, y, "KEYS")
            self._blit_text("Space play/pause · N step · S skip", x, y, self.mono, (100, 110, 125))
            y += 14
            self._blit_text("R reset · L auto-loop · P paths", x, y, self.mono, (100, 110, 125))
            y += 14
            self._blit_text("+/- speed · Esc quit", x, y, self.mono, (100, 110, 125))

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
        gap = 6
        n = max(1, len(items))
        btn_w = (width - gap * (n - 1)) // n
        for i, (action, label, flag) in enumerate(items):
            bx = x + i * (btn_w + gap)
            rect = pygame.Rect(bx, y, btn_w, height)
            hover = self._hover_action == action
            # For transport play/pause, flag means "this button is the useful action".
            # For toggles/speed, flag means selected/active.
            if action in {"auto_loop", "show_paths", "show_labels"} or action.startswith("speed_"):
                active = flag
                enabled = True
            elif action in {"play", "pause"}:
                active = flag
                enabled = True
            else:
                active = False
                enabled = True
            self._draw_button(rect, label, active=active, hover=hover, enabled=enabled)
            self._buttons.append(UiButton(action, rect, label, active=active, enabled=enabled))
        return y + height + 6

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
        if not enabled:
            fill, border, text = (230, 232, 238), (200, 204, 214), (160, 165, 175)
        elif active:
            fill, border, text = (40, 120, 220), (30, 100, 190), (255, 255, 255)
        elif hover:
            fill, border, text = (225, 232, 245), (100, 140, 200), (30, 40, 55)
        else:
            fill, border, text = (240, 243, 248), (175, 184, 200), (40, 48, 62)
        pygame.draw.rect(self.screen, fill, rect, border_radius=8)
        pygame.draw.rect(self.screen, border, rect, width=1, border_radius=8)
        surface = self.small.render(label, True, text)
        self.screen.blit(
            surface,
            (rect.centerx - surface.get_width() // 2, rect.centery - surface.get_height() // 2),
        )

    def _section_title(self, x: int, y: int, title: str) -> int:
        self._blit_text(title, x, y, self.small, (30, 110, 200))
        return y + 18

    def _section_rule(self, x: int, y: int, width: int) -> int:
        pygame = self.pygame
        pygame.draw.line(self.screen, (210, 216, 226), (x, y), (x + width, y), 1)
        return y + 10

    def _kv_row(self, x: int, y: int, width: int, label: str, value: str) -> int:
        self._blit_text(label, x, y, self.small, (100, 110, 125))
        surface = self.small.render(str(value), True, (30, 36, 48))
        self.screen.blit(surface, (x + width - surface.get_width(), y))
        return y + 17

    def _player_card(
        self, x: int, y: int, width: int, side: str, elo: int, skill: int, think_s: float
    ) -> int:
        pygame = self.pygame
        accent = (240, 240, 245) if side == "White" else (55, 60, 72)
        outline = (180, 185, 195) if side == "White" else (55, 60, 72)
        pygame.draw.circle(self.screen, accent, (x + 8, y + 8), 6)
        pygame.draw.circle(self.screen, outline, (x + 8, y + 8), 6, width=1)
        self._blit_text(side, x + 20, y, self.font, (30, 36, 48))
        detail = f"Elo {elo}  ·  skill {skill}  ·  {think_s:0.1f}s"
        self._blit_text(detail, x + 20, y + 16, self.small, (100, 110, 125))
        return y + 38

    def _progress_bar(self, x: int, y: int, width: int, fraction: float) -> int:
        pygame = self.pygame
        h = 8
        pygame.draw.rect(self.screen, (220, 226, 236), pygame.Rect(x, y, width, h), border_radius=4)
        fill = max(0, min(1.0, fraction))
        if fill > 0:
            pygame.draw.rect(
                self.screen,
                (40, 130, 230),
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

    def _draw_move_list(self, x: int, y: int, width: int, moves: list[str], max_lines: int = 10) -> int:
        if not moves:
            self._blit_text("No moves yet", x, y, self.small, (130, 138, 150))
            return y + 18
        pairs: list[str] = []
        for i in range(0, len(moves), 2):
            num = i // 2 + 1
            white = moves[i]
            black = moves[i + 1] if i + 1 < len(moves) else ""
            pairs.append(f"{num}. {white} {black}".strip())
        shown = pairs[-max_lines:]
        for line in shown:
            self._blit_text(line, x, y, self.mono, (40, 48, 62))
            y += 15
        if len(pairs) > max_lines:
            self._blit_text(f"… {len(pairs) - max_lines} earlier", x, y, self.small, (120, 128, 140))
            y += 15
        return y

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
