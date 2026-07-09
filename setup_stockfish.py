"""Download Stockfish into the project root as stockfish.exe (Windows AVX2 build)."""

from __future__ import annotations

import os
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path

STOCKFISH_URL = (
    "https://github.com/official-stockfish/Stockfish/releases/latest/download/"
    "stockfish-windows-x86-64-avx2.zip"
)
ZIP_PATH = Path("stockfish.zip")
EXTRACT_PATH = Path("stockfish_dir")
TARGET = Path("stockfish.exe")


def setup_stockfish() -> int:
    if sys.platform != "win32":
        print(
            "This helper downloads the Windows AVX2 build only. "
            "On other platforms, install Stockfish and pass --engine /path/to/stockfish."
        )
        return 1

    print(f"Downloading Stockfish from {STOCKFISH_URL}...")
    try:
        urllib.request.urlretrieve(STOCKFISH_URL, ZIP_PATH)
        print("Download complete. Extracting...")

        if EXTRACT_PATH.exists():
            shutil.rmtree(EXTRACT_PATH)
        with zipfile.ZipFile(ZIP_PATH, "r") as zip_ref:
            zip_ref.extractall(EXTRACT_PATH)

        exe_path = None
        for root, _dirs, files in os.walk(EXTRACT_PATH):
            for file in files:
                if file.endswith(".exe") and "stockfish" in file.lower():
                    exe_path = Path(root) / file
                    break
            if exe_path:
                break

        if exe_path is None:
            print("Could not find Stockfish executable in the zip file.")
            return 1

        if TARGET.exists():
            TARGET.unlink()
        os.rename(exe_path, TARGET)
        print(f"Moved executable to {TARGET.resolve()}")

        ZIP_PATH.unlink(missing_ok=True)
        shutil.rmtree(EXTRACT_PATH, ignore_errors=True)
        print("Cleanup complete.")
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI helper should surface any failure
        print(f"Error setting up stockfish: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(setup_stockfish())
