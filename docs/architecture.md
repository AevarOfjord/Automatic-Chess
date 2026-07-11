# Architecture

## Control split

| Layer | Responsibility |
|-------|----------------|
| PC (`chess_robot`) | Chess rules, physical token identity, move decomposition, IK, puck paths, camera occupancy check, recovery policy |
| ESP32 gateway | USB newline JSON ↔ ESP-NOW routing |
| ESP32 arms | Homing, trajectory execution, magnet + pickup sensor |

The PC never trusts the arms for chess correctness. Arms report motion completion and basic telemetry only.

## Data flow for one move

```
chess.Board + PhysicalInventory
        │
        ▼
ChessMovePlanner ──► list[PhysicalTransfer]
        │
        ▼
for each transfer:
   park opposite arm (keep-out)
   PuckTrajectoryPlanner (XY around other pucks)
   planar 3R IK waypoints
   GatewayTransport / MockGatewayTransport
   SET_MAGNET on/off
        │
        ▼
BoardVision.verify_expected (occupancy)
        │
        ▼
commit board + inventory
```

## Important invariants

1. **32 physical tokens only.** Promotion changes `logical_type`, not the puck.
2. **Dead racks are ordered.** Captures fill `W1…` / `B1…` deterministically.
3. **Serialized workspace.** Only one arm works the table; the other is parked.
3b. **No knight jumps.** Fixed-height magnets slide in XY only; if a legal chess move has no collision-free corridor, the game layer skips it and tries another legal move (same as the visual twin).
4. **Wire budget.** JSON commands ≤ 240 bytes (ESP-NOW), trajectories chunked in 4 waypoints.
5. **Vision is occupancy-only.** Identity recovery is operator + inventory, not CV class labels.

## Module map

| Module | Role |
|--------|------|
| `game.py` | Game loop, players, fault latch |
| `planning.py` | Legal move → physical transfers; reset evacuate/place |
| `inventory.py` | Token IDs, locations, dead racks |
| `trajectory.py` | Collision-free puck XY graph search |
| `geometry.py` | Board mm frame + planar 3R inverse kinematics |
| `hardware.py` | Lock, park policy, retries, magnet sequence |
| `protocol.py` | Command/response JSON + journal |
| `transport.py` | Serial / mock gateway |
| `vision.py` | Homography + empty-reference occupancy |
| `visual_*` | Digital twin (same planners, animated time) |

## Runtime configuration

Defaults live in `RobotConfig`. Override via:

- CLI (`--port`, …)
- Environment: `CHESS_ROBOT_PORT`, `CHESS_ROBOT_BAUD`, `CHESS_ROBOT_TIMEOUT_S`, `CHESS_ROBOT_RETRIES`, `CHESS_ROBOT_JOURNAL`
- Verbose logs: `python -m chess_robot -v simulate …`

See also [hardware.md](hardware.md) and [fault_recovery.md](fault_recovery.md).
