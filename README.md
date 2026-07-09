# Dual-SCARA Chess Robot

Software-first build for two opposing SCARA chess robots.  The PC is the big brain: it owns the chess state, physical inventory, move planning, kinematics, validation, and recovery.  The ESP32s later become motion executors.

The physical board model is a 12 column × 8 row magnetic grid.  Each cell is 50 mm × 50 mm.  The center 8 columns are the playable chessboard, while the 2 left columns store `W1...W16` and the 2 right columns store `B1...B16`.

## Start with the visual simulator

From this folder:

```powershell
.\venv\Scripts\python.exe visual_simulation.py
```

Or through the package CLI:

```powershell
.\venv\Scripts\python.exe -m chess_robot visual
```

By default, the visual simulator uses `stockfish.exe` from this folder.  The two sides intentionally use different strength settings so the games are useful and varied rather than two identical perfect engines.

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

## What the simulator shows

- 400 × 400 mm board with 50 mm squares.
- Two 300 mm + 300 mm SCARA arms.
- Arm bases, elbow links, fixed-height tool/magnet position, and puck movement.
- Dead-piece storage built into the extra board columns: `W1...W16` on the left and `B1...B16` on the right.
- Real chess move decomposition: captures, normal moves, castling, en passant, promotion, and reset plans use the same planner intended for hardware.
- Promotion uses the same physical pawn token.  The PC remembers when that pawn is acting as a queen, rook, bishop, or knight, so the physical set remains a normal 32-piece chess set.

## Dead-piece rack rule

Captured pieces are placed deterministically, never randomly.

- If the White-side robot captures a piece, it fills `W1`, then `W2`, then `W3`, and so on.
- If the Black-side robot captures a piece, it fills `B1`, then `B2`, then `B3`, and so on.
- The PC remembers the exact physical token in every marked slot, for example `W2 = black pawn from c7`.
- During reset, the PC uses that identity map to return every physical token to its original square.

This means the physical table can be simple and repeatable: marked slots only need labels, not piece-type assignments.

The simulator is intentionally top-down.  It is for validating reachability, flow, sequencing, and human understanding before designing brackets, wiring motors, or flashing ESP32s.

The arm model is fixed-height: there is no Z pickup axis.  The electromagnet turns on at the puck center, carries the puck through XY lanes, and turns off at the destination.  Weak magnets in each board cell help snap the steel-insert pucks back to cell centers after release.

## Non-visual software checks

```powershell
.\venv\Scripts\python.exe -m chess_robot reachability
.\venv\Scripts\python.exe -m chess_robot simulate --random --games 1 --max-plies 40
.\venv\Scripts\python.exe -m unittest discover -v
```

The first hardware milestone should wait until the visual simulator and planner tests are boringly reliable.
