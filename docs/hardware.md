# Hardware bring-up checklist

PC software is ready for mock and visual validation. Flash firmware only after planner tests are boringly reliable.

## Repository layout

| Path | Role |
|------|------|
| `firmware/esp32_gateway.ino` | USB serial ↔ ESP-NOW bridge |
| `firmware/esp32_arm_receiver.ino` | Per-arm motion executor (flash twice: WHITE / BLACK) |
| `chess_robot/` | PC brain: chess, inventory, planning, kinematics, vision |

## Network topology

```
PC  --USB serial JSON-->  ESP32 Gateway  --ESP-NOW-->  WHITE arm ESP32
                                         --ESP-NOW-->  BLACK arm ESP32
```

- PC owns chess state, inventory, trajectories, and recovery policy.
- Gateway only routes packets; **DONE/FAULT come from the arm**, not radio ACK alone.
- Wire frames are newline-delimited JSON, max **240 bytes** (ESP-NOW payload budget).

## Before first power-on

1. Set unique Wi‑Fi MACs for gateway peer list and each arm (`GATEWAY_MAC`, `WHITE_ARM_MAC`, `BLACK_ARM_MAC`).
2. Flash `esp32_arm_receiver.ino` once with `#define ARM_ID "WHITE"`, once with `"BLACK"`.
3. Confirm stepper pins, home switches, e-stop, magnet driver, and pickup sensor match the sketch.
4. Calibrate `J1_STEPS_PER_DEG` / `J2_STEPS_PER_DEG` / `J3_STEPS_PER_DEG` for your gearing and microstepping (or PWM mapping for MG995 servos).
5. Park poses and base positions in `chess_robot/config.py` must match the physical table (600×400 mm grid, 50 mm cells).
6. Default arm geometry is a **planar 3R** with unequal links **200 / 160 / 180 mm**, bases **50 mm** off the long table edges, and **180°** joint windows per MG995-class servo.

### Grid labels (mark these on the table)

| Region | Columns | Cell names |
|--------|---------|------------|
| White dead rack | C1–C2 | W1…W16 (W1 at top) |
| Empty separator | C3 | empty lane |
| Chess play area | C4–C11 | a1…h8 (a1 near White base / bottom) |
| Empty separator | C12 | empty lane |
| Black dead rack | C13–C14 | B1…B16 (B1 at top) |

Rows **R1…R8** run bottom → top (same as chess ranks). Columns **C1…C14** run left → right.

## PC serial settings

Default in `RobotConfig`:

- Port: `COM3` (change with `--port` on `python -m chess_robot run`)
- Baud: `115200`
- Response timeout: `20 s`
- Command retries: `1` (timeouts only)

## Motion model

- Fixed tool height (no Z lift axis).
- Three rotary joints per arm (shoulder / elbow / wrist); wire waypoints are
  `[shoulder°, elbow°, wrist°, z_mm, speed, acceleration]`.
- Electromagnet on at source center → planar XY path → magnet off at destination.
- Weak board-cell magnets help snap steel-insert pucks to cell centers after release.
- **Keep-out policy:** only one arm works the table at a time; the opposite arm is parked first
  in a folded rest pose outside the board.

## Camera

### Electromagnet timing

Each transfer stops at the source before switching the electromagnet on, then
holds position for **0.5 seconds**. At the destination it switches the magnet
off and holds for another **0.5 seconds** before parking. The arm controller
enforces these settles before it reports `DONE`, so a delayed PC or USB packet
cannot shorten them.

Tune the defaults only after measured pickup/release trials with
`CHESS_ROBOT_PICKUP_SETTLE_S` and `CHESS_ROBOT_RELEASE_SETTLE_S`.

```powershell
.\venv\Scripts\python.exe -m chess_robot calibrate-camera --output runtime_data/camera_calibration.npz
```

Vision verifies **occupancy**, not piece identity. The PC inventory remains authoritative for which physical token is which.

## Safe first motion sequence

1. `python -m chess_robot reachability`
2. `python -m unittest discover -v`
3. Visual twin: `python -m chess_robot visual --random --paused`
4. Mock stack: `python -m chess_robot simulate --random --games 1 --max-plies 20`
5. Home both arms with no pieces on the board.
6. Single transfer of one known puck with magnet and sensor supervised.
7. Only then: `python -m chess_robot run --port COMx`

See also [build_dimensions.md](build_dimensions.md) for the full physical cut list and [fault_recovery.md](fault_recovery.md).
