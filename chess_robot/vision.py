from __future__ import annotations

from pathlib import Path

import chess
import cv2
import numpy as np


class VisionError(RuntimeError):
    pass


class BoardVision:
    """Overhead-camera occupancy detector; piece identity remains PC-authoritative."""

    def __init__(
        self,
        camera_index: int = 0,
        use_mock: bool = True,
        board_size_px: int = 800,
        difference_threshold: float = 18.0,
    ) -> None:
        self.use_mock = use_mock
        self.board_size_px = board_size_px
        self.difference_threshold = difference_threshold
        self.homography: np.ndarray | None = None
        self.empty_reference: np.ndarray | None = None
        self.mock_next_result: bool | None = None
        self.last_missing: set[str] = set()
        self.last_extra: set[str] = set()
        self.cap: cv2.VideoCapture | None = None
        if not use_mock:
            self.cap = cv2.VideoCapture(camera_index)
            if not self.cap.isOpened():
                raise VisionError(f"could not open camera index {camera_index}")

    def set_mock_result_once(self, valid: bool) -> None:
        self.mock_next_result = valid

    def calibrate_from_corners(self, corners: list[tuple[float, float]]) -> None:
        if len(corners) != 4:
            raise ValueError("corners must be top-left, top-right, bottom-right, bottom-left")
        src = np.asarray(corners, dtype=np.float32)
        size = self.board_size_px - 1
        dst = np.asarray([(0, 0), (size, 0), (size, size), (0, size)], dtype=np.float32)
        self.homography = cv2.getPerspectiveTransform(src, dst)

    def calibrate_interactive(self) -> None:
        if self.use_mock:
            return
        frame = self._read_frame()
        corners: list[tuple[float, float]] = []
        window = "Chess Robot Calibration: TL, TR, BR, BL"

        def click(event: int, x: int, y: int, _flags: int, _param: object) -> None:
            if event == cv2.EVENT_LBUTTONDOWN and len(corners) < 4:
                corners.append((x, y))

        cv2.namedWindow(window)
        cv2.setMouseCallback(window, click)
        while len(corners) < 4:
            display = frame.copy()
            for x, y in corners:
                cv2.circle(display, (int(x), int(y)), 6, (0, 255, 0), -1)
            cv2.imshow(window, display)
            if cv2.waitKey(20) & 0xFF == 27:
                cv2.destroyWindow(window)
                raise VisionError("camera calibration cancelled")
        cv2.destroyWindow(window)
        self.calibrate_from_corners(corners)

    def capture_empty_reference(self) -> None:
        if self.use_mock:
            return
        self.empty_reference = self.warped_board()

    def save_calibration(self, path: str | Path) -> None:
        if self.homography is None or self.empty_reference is None:
            raise VisionError("homography and empty-board reference are both required")
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(path, homography=self.homography, empty_reference=self.empty_reference)

    def load_calibration(self, path: str | Path) -> None:
        data = np.load(Path(path))
        self.homography = data["homography"]
        self.empty_reference = data["empty_reference"]

    def _read_frame(self) -> np.ndarray:
        if self.cap is None:
            raise VisionError("camera is not active")
        ok, frame = self.cap.read()
        if not ok:
            raise VisionError("camera frame read failed")
        return frame

    def warped_board(self) -> np.ndarray:
        if self.homography is None:
            raise VisionError("board corners have not been calibrated")
        return cv2.warpPerspective(
            self._read_frame(), self.homography, (self.board_size_px, self.board_size_px)
        )

    def detect_occupancy(self) -> set[str]:
        if self.empty_reference is None:
            raise VisionError("capture an empty-board reference before detecting pieces")
        current = self.warped_board()
        current_gray = cv2.cvtColor(current, cv2.COLOR_BGR2GRAY)
        reference_gray = cv2.cvtColor(self.empty_reference, cv2.COLOR_BGR2GRAY)
        size = self.board_size_px // 8
        occupied: set[str] = set()
        for display_row in range(8):
            for file_index in range(8):
                y0, y1 = display_row * size, (display_row + 1) * size
                x0, x1 = file_index * size, (file_index + 1) * size
                # Ignore borders where neighboring square colors and board lines dominate.
                inset = max(3, size // 10)
                current_cell = current_gray[y0 + inset : y1 - inset, x0 + inset : x1 - inset]
                reference_cell = reference_gray[y0 + inset : y1 - inset, x0 + inset : x1 - inset]
                score = float(np.mean(cv2.absdiff(current_cell, reference_cell)))
                if score >= self.difference_threshold:
                    rank_index = 7 - display_row
                    occupied.add(chess.square_name(chess.square(file_index, rank_index)))
        return occupied

    def verify_expected(self, board: chess.Board) -> bool:
        expected = {chess.square_name(square) for square in board.piece_map()}
        if self.use_mock:
            valid = True if self.mock_next_result is None else self.mock_next_result
            self.mock_next_result = None
            self.last_missing = set() if valid else set(expected)
            self.last_extra = set()
            return valid
        actual = self.detect_occupancy()
        self.last_missing = expected - actual
        self.last_extra = actual - expected
        return not self.last_missing and not self.last_extra

    def release(self) -> None:
        if self.cap is not None:
            self.cap.release()
