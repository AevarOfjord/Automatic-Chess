# Wiring diagram

Pin numbers match the defaults in `firmware/esp32_arm_receiver.ino` and
`firmware/esp32_gateway.ino`. Change the sketch constants if you reassign pins.

---

## 1. System overview

```
                         ┌──────────────────────┐
                         │   PC (chess_robot)   │
                         │  USB serial 115200   │
                         └──────────┬───────────┘
                                    │ USB
                         ┌──────────▼───────────┐
                         │  ESP32 GATEWAY       │
                         │  (USB ↔ ESP-NOW)     │
                         └─────┬──────────┬─────┘
                    ESP-NOW    │          │    ESP-NOW
              ┌────────────────┘          └────────────────┐
              │                                            │
   ┌──────────▼──────────┐                      ┌──────────▼──────────┐
   │ ESP32 ARM WHITE     │                      │ ESP32 ARM BLACK     │
   │ #define ARM_ID      │                      │ #define ARM_ID      │
   │   "WHITE"           │                      │   "BLACK"           │
   └──────────┬──────────┘                      └──────────┬──────────┘
              │                                            │
     J1 J2 J3 │ magnet / sensors / e-stop         same as white
              ▼                                            ▼
         WHITE 3R arm                               BLACK 3R arm
```

| Node | Role |
|------|------|
| **PC** | Chess, planning, IK, JSON commands |
| **Gateway ESP32** | USB serial ↔ ESP-NOW; does **not** decide DONE |
| **Arm ESP32 ×2** | Motion, magnet, home/estop/pickup; emits DONE/FAULT |

Radio delivery is not completion — only the arm reports `DONE` / `FAULT`.

---

## 2. Power distribution (recommended)

MG995-class servos need a **stiff 5–6 V supply** (several amps peak with 6 servos).
Do **not** power servos from the ESP32 5 V pin or USB alone.

```
  AC mains
     │
     ├─► PSU A  5 V / ≥5 A  ──┬── WHITE J1, J2, J3 servo power
     │                        └── BLACK J1, J2, J3 servo power
     │                              (common GND to both arms + logic)
     │
     ├─► USB 5 V  ────────────┬── PC → Gateway ESP32 (logic only)
     │                        └── optional: arm ESP32 VIN/USB (logic only)
     │
     └─► Magnet supply ───────► per-arm magnet driver (often 12 V or 24 V;
                                  use a MOSFET / relay module, not a GPIO)

  ★ Common ground: tie PSU A GND, magnet supply GND, and all ESP32 GND together.
  ★ Put bulk capacitors near each arm’s servo power rails (e.g. 1000 µF).
```

| Rail | Feeds | Notes |
|------|--------|--------|
| Servo 5–6 V | 6× MG995 (3 per arm) | High current; thick wire |
| Logic 5 V / 3.3 V | ESP32 boards | From USB or regulated 5 V → ESP32 |
| Magnet rail | Electromagnet coil | Through driver; GPIO only switches FET gate |

---

## 3. Gateway ESP32

No joint I/O — only USB and Wi‑Fi (ESP-NOW).

```
  PC USB  ──►  ESP32 USB (or UART0 if using external USB-serial)

  Sketch: firmware/esp32_gateway.ino
  Serial.begin(115200)

  Peers (edit before flash):
    WHITE_ARM_MAC[] = { ... }   // real MAC of white arm ESP32
    BLACK_ARM_MAC[] = { ... }   // real MAC of black arm ESP32
```

| Connection | Notes |
|------------|--------|
| USB to PC | Default COM port in software: `COM3` (override with `--port`) |
| Antenna / Wi‑Fi | STA mode; same channel as arms |

---

## 4. Arm ESP32 pin map (WHITE and BLACK identical)

Flash `firmware/esp32_arm_receiver.ino` twice:

1. `#define ARM_ID "WHITE"` + set `GATEWAY_MAC[]`
2. `#define ARM_ID "BLACK"` + same `GATEWAY_MAC[]`

### GPIO table (default sketch — MG995 PWM servos)

Absolute-position servos: **one PWM signal per joint, no DIR / ENABLE / home switches.**

| Signal | GPIO | Direction | Connects to |
|--------|------|-----------|-------------|
| **J1 signal** | **13** | OUT (PWM) | Shoulder MG995 signal |
| **J2 signal** | **25** | OUT (PWM) | Elbow MG995 signal |
| **J3 signal** | **26** | OUT (PWM) | Wrist MG995 signal |
| **E-STOP** | **32** | IN pull-up | E-stop NC → GND when pressed (fault) |
| **PICKUP sensor** | **33** | IN pull-up | Sensor → GND when piece detected |
| **MAGNET** | **23** | OUT | Magnet driver input (HIGH = on) |
| **GND** | GND | — | Common with servos, supplies, switches |
| **3V3 / 5V** | — | — | Logic only; not servo power |

The firmware maps each logical joint angle to a servo pulse width via the
`jointCal` table (`midUs` / `usPerDeg` / `dir`); tune those per servo so the
commanded angle matches the true mechanical angle.

### Per-joint wiring (one joint shown; repeat for J1/J2/J3)

```
   ESP32 GPIO (13 / 25 / 26) ──► MG995 signal (orange)
                    servo 5–6 V PSU (+) ──► MG995 V+ (red)
                    common GND        ──► MG995 GND (brown)

   ★ Servo power comes from the PSU, never the ESP32 5V pin.
   ★ Tie servo-PSU GND to ESP32 GND so the PWM signal shares a reference.
```

### E-stop / pickup (active low to GND)

```
   ESP32 GPIO (ESTOP 32 / PICKUP 33)
        │
        ├── internal INPUT_PULLUP
        │
        └── switch/sensor ──► GND   (closed = LOW = asserted)

   Sketch logic:
     ESTOP : LOW → enter FAULT ("emergency stop")
     PICKUP: LOW → telemetry pickup=true after magnet settle
```

### Electromagnet

```
   ESP32 GPIO 23 (MAGNET)
        │
        ▼ gate
   ┌────────────┐     magnet rail (+)
   │ N-MOSFET / │◄──── or relay module VCC
   │ driver mod │
   └─────┬──────┘
         │ drain / switched side
         ▼
      electromagnet coil
         │
        GND (common)

   Flyback diode across coil if driving an inductive load directly.
   Default: HIGH = magnet ON, LOW = OFF (after SET_MAGNET).
```

---

## 5. Full arm cable sketch (one arm)

```
                    ┌──────────────────────────────────────┐
                    │           ESP32 (ARM)                │
                    │                                      │
   Gateway ESP-NOW  │  Wi-Fi antenna                       │
                    │                                      │
         13 ────────┼─► J1 signal ──► Shoulder MG995       │
         25 ────────┼─► J2 signal ──► Elbow MG995          │
         26 ────────┼─► J3 signal ──► Wrist MG995          │
                    │                                      │
         32 ────────┼─► E-stop                             │
         33 ────────┼─► Pickup sensor                      │
         23 ────────┼─► Magnet driver                      │
        GND ────────┼─► common ground (servos + logic)     │
                    └──────────────────────────────────────┘

   Servo V+ (red) goes to the 5–6 V servo PSU, not the ESP32.
```

Duplicate this block for the second arm with its own ESP32, MAC, and `ARM_ID`.

---

## 6. MAC / identity checklist

| Board | Setting | Where |
|-------|---------|--------|
| Gateway | `WHITE_ARM_MAC`, `BLACK_ARM_MAC` | `esp32_gateway.ino` |
| White arm | `ARM_ID "WHITE"`, `GATEWAY_MAC` | `esp32_arm_receiver.ino` |
| Black arm | `ARM_ID "BLACK"`, `GATEWAY_MAC` | same sketch, second flash |

Print MACs once with a small Wi‑Fi sketch (`WiFi.macAddress()`) and paste real values (placeholder `0xFF…` / `0xAA…` will not route).

---

## 7. Signal summary (both arms)

| Function | White arm ESP32 | Black arm ESP32 |
|----------|-----------------|-----------------|
| Shoulder signal | GPIO 13 | same pin numbers on second board |
| Elbow signal | GPIO 25 | same |
| Wrist signal | GPIO 26 | same |
| E-stop | GPIO 32 | same (or shared loop if desired) |
| Pickup | GPIO 33 | same |
| Magnet | GPIO 23 | same |
| Comms | ESP-NOW ↔ gateway | ESP-NOW ↔ gateway |

---

## 8. Bring-up order (electrical)

1. Power **logic only** (gateway + one arm ESP32 on USB). Confirm the radio link with `python -m chess_robot status`.
2. Add **servo PSU** with no load; check 5–6 V under a single servo.
3. Wire **one joint** signal + power; nudge it with `python -m chess_robot jog --joint shoulder --deg 5`.
4. Wire e-stop, magnet, pickup; verify FAULT on e-stop (`status` reports it) and pickup telemetry after `magnet --state on`.
5. Repeat for second arm; set MACs; full dual-arm keep-out test.

---

## 9. Related docs

- [Hardware bring-up](hardware.md)
- [Build dimensions](build_dimensions.md)
- [Architecture](architecture.md)
- [Fault recovery](fault_recovery.md)
