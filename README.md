# Dual-Arm Chess Robot

Software-first build for two opposing planar 3R chess robots (MG995-class 180° joints). The PC is the big brain: it owns the chess state, physical inventory, move planning, kinematics, validation, and recovery. The ESP32s are motion executors.

Repository: [AevarOfjord/Automatic-Chess](https://github.com/AevarOfjord/Automatic-Chess) · License: [MIT](LICENSE)

The physical board model is **640 × 400 mm**: 50 mm piece cells plus **20 mm** empty gaps between racks and the board.

| Region | Piece columns | Labels / size |
|--------|---------------|---------------|
| White dead rack | **C1–C2** | **W1…W16** (top → bottom), 50 mm cells |
| Empty separator | — | **20 mm** gap |
| Chessboard | **C3–C10** | **a1…h8**, 50 mm cells |
| Empty separator | — | **20 mm** gap |
| Black dead rack | **C11–C12** | **B1…B16**, 50 mm cells |

Table rows are **R1…R8** bottom → top (same direction as chess ranks and +Y mm).

## Setup

Using Make (recommended):

```powershell
make install
make run
```

- `make install` — create `./venv` if needed, then install `requirements.txt`
- `make run` — run `install` if needed, then launch the visual simulator

Manual setup:

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

By default the visual simulator uses `stockfish.exe` from this folder. Both sides use the same strength (≈1700 Elo, skill 10, **1 s** per move) for a fair match.

Useful options:

```powershell
.\venv\Scripts\python.exe -m chess_robot visual --seed 7 --speed 2
.\venv\Scripts\python.exe -m chess_robot visual --paused
.\venv\Scripts\python.exe -m chess_robot visual --max-plies 20
.\venv\Scripts\python.exe -m chess_robot visual --engine stockfish.exe --white-elo 1800 --black-elo 1300
.\venv\Scripts\python.exe -m chess_robot visual --random
```

### Control board (right panel)

Under a shared header, three columns:

| Column | Contents |
|--------|----------|
| **Moves** | Scoresheet: `1. White: E2 to E4`, `2. Black: E7 to E5`, … |
| **Observe** | Live telemetry — now running, game, engines, motion, arm state, status |
| **Controls** | Play / Pause / Step / Skip / Reset / Next game, speed, modes, keys |

Hover a column and use the mouse wheel (or PgUp/PgDn) to scroll that column only.

Keyboard: `Space` play/pause · `N` step · `S` skip · `R` reset · `L` auto-loop · `P` paths · `+/-` speed · `Esc` quit.

## Architecture

```
chess_robot/
  game.py            # game loop, Stockfish/random players, fault state
  planning.py        # chess move → physical transfers (capture, castle, EP, promo)
  inventory.py       # 32-token identity map + dead racks
  trajectory.py      # puck XY paths around other pucks
  geometry.py        # board layout + planar 3R IK
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
.\venv\Scripts\python.exe -m chess_robot optimize-geometry
.\venv\Scripts\python.exe -m chess_robot simulate --random --games 1 --max-plies 40
.\venv\Scripts\python.exe -m unittest discover -v
```

`optimize-geometry` searches the mirrored 3R design space (unequal link lengths, 180° joint
windows, 55 mm base setback) and writes `runtime_data/geometry_optimization.json`. Default
geometry is **200 / 160 / 180 mm** links. Certification covers the operational grid and each
horizontal, vertical, and diagonal neighboring-grid route.

## Docs

- [Architecture](docs/architecture.md)
- [Hardware bring-up](docs/hardware.md)
- [Wiring diagram](docs/wiring.md)
- [Build dimensions](docs/build_dimensions.md)
- [Fault recovery](docs/fault_recovery.md)

Environment overrides: `CHESS_ROBOT_PORT`, `CHESS_ROBOT_BAUD`, `CHESS_ROBOT_TIMEOUT_S`, `CHESS_ROBOT_RETRIES`, `CHESS_ROBOT_JOURNAL`.

Verbose logs: `python -m chess_robot -v simulate --random --seed 1 --max-plies 8`

The first hardware milestone should wait until the visual simulator and planner tests are boringly reliable.
