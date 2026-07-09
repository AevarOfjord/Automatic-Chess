# Dual-SCARA Chess Robot

Software-first build for two opposing SCARA chess robots. The PC is the big brain: it owns the chess state, physical inventory, move planning, kinematics, validation, and recovery. The ESP32s are motion executors.

Repository: [AevarOfjord/Automatic-Chess](https://github.com/AevarOfjord/Automatic-Chess) · License: [MIT](LICENSE)

The physical board model is a 12 × 8 magnetic grid (50 mm cells):

| Region | Columns | Labels |
|--------|---------|--------|
| White dead rack | **C1–C2** | **W1…W16** (top → bottom) |
| Chessboard | **C3–C10** | **a1…h8** (files a–h, ranks 1–8 bottom → top) |
| Black dead rack | **C11–C12** | **B1…B16** (top → bottom) |

Table rows are **R1…R8** bottom → top (same direction as chess ranks and +Y mm).

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python setup_stockfish.py
```

## Start with the visual simulator

Preferred entry point:

```powershell
.\venv\Scripts\python.exe -m chess_robot visual
```

By default the visual simulator uses `stockfish.exe` from this folder. The two sides use different strength settings so games stay varied.

Useful options:

```powershell
.\venv\Scripts\python.exe -m chess_robot visual --seed 7 --speed 2
.\venv\Scripts\python.exe -m chess_robot visual --paused
.\venv\Scripts\python.exe -m chess_robot visual --max-plies 20
.\venv\Scripts\python.exe -m chess_robot visual --engine stockfish.exe --white-elo 1800 --black-elo 1300
.\venv\Scripts\python.exe -m chess_robot visual --random
```

Controls inside the simulator:

- `Space` pauses/resumes.
- `N` advances one planned move while paused.
- `R` requests a board reset.
- `+` / `-` changes animation speed.
- `Esc` or `Q` exits.

## Architecture

```
chess_robot/
  game.py            # game loop, Stockfish/random players, fault state
  planning.py        # chess move → physical transfers (capture, castle, EP, promo)
  inventory.py       # 32-token identity map + dead racks
  trajectory.py      # puck XY paths around other pucks
  geometry.py        # board layout + SCARA IK
  hardware.py        # serialized dual-arm execution + keep-out park
  transport.py       # serial / mock gateway
  protocol.py        # JSON wire format + command journal
  vision.py          # occupancy verification
  visual_simulator.py
  visual_render.py
firmware/
  esp32_gateway.ino
  esp32_arm_receiver.ino
docs/
  hardware.md
  fault_recovery.md
```

Promotion keeps the same physical pawn puck; `logical_type` on the token records queen/rook/bishop/knight until reset.

## Dead-piece rack rule

Captured pieces are placed deterministically, never randomly.

- White-side robot captures fill `W1`, then `W2`, …
- Black-side robot captures fill `B1`, then `B2`, …
- Reset uses the identity map to return every token home.

## Non-visual software checks

```powershell
.\venv\Scripts\python.exe -m chess_robot reachability
.\venv\Scripts\python.exe -m chess_robot simulate --random --games 1 --max-plies 40
.\venv\Scripts\python.exe -m unittest discover -v
```

## Docs

- [Architecture](docs/architecture.md)
- [Hardware bring-up](docs/hardware.md)
- [Fault recovery](docs/fault_recovery.md)

Environment overrides: `CHESS_ROBOT_PORT`, `CHESS_ROBOT_BAUD`, `CHESS_ROBOT_TIMEOUT_S`, `CHESS_ROBOT_RETRIES`, `CHESS_ROBOT_JOURNAL`.

Verbose logs: `python -m chess_robot -v simulate --random --seed 1 --max-plies 8`

The first hardware milestone should wait until the visual simulator and planner tests are boringly reliable.

## Legacy root scripts

Thin compatibility wrappers (`game_manager.py`, `robot_controller.py`, `vision_validator.py`, `visual_simulation.py`) remain for older imports. Prefer `python -m chess_robot …` for new work.
