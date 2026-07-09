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
4. Calibrate `J1_STEPS_PER_DEG` / `J2_STEPS_PER_DEG` for your gearing and microstepping.
5. Park poses and base positions in `chess_robot/config.py` must match the physical table (600×400 mm grid, 50 mm cells).

## PC serial settings

Default in `RobotConfig`:

- Port: `COM3` (change with `--port` on `python -m chess_robot run`)
- Baud: `115200`
- Response timeout: `20 s`
- Command retries: `1` (timeouts only)

## Motion model

- Fixed tool height (no Z lift axis).
- Electromagnet on at source center → planar XY path → magnet off at destination.
- Weak board-cell magnets help snap steel-insert pucks to cell centers after release.
- **Keep-out policy:** only one arm works the table at a time; the opposite arm is parked first.

## Camera

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

See also [fault_recovery.md](fault_recovery.md).
