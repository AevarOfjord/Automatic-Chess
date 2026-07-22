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

### GPIO table (default sketch)

| Signal | GPIO | Direction | Connects to |
|--------|------|-----------|-------------|
| **J1 STEP / PWM** | **12** | OUT | Shoulder driver STEP or servo signal* |
| **J1 DIR** | **14** | OUT | Shoulder DIR (if step/dir driver) |
| **J2 STEP / PWM** | **27** | OUT | Elbow driver STEP or servo signal* |
| **J2 DIR** | **26** | OUT | Elbow DIR |
| **J3 STEP / PWM** | **25** | OUT | Wrist driver STEP or servo signal* |
| **J3 DIR** | **33** | OUT | Wrist DIR |
| **ENABLE** | **13** | OUT | Driver enable (active per driver datasheet) |
| **J1 HOME** | **32** | IN pull-up | Shoulder home switch → GND when hit |
| **J2 HOME** | **35** | IN pull-up | Elbow home switch → GND when hit |
| **J3 HOME** | **34** | IN pull-up | Wrist home switch → GND when hit |
| **E-STOP** | **39** | IN pull-up | E-stop NC → GND when pressed (fault) |
| **PICKUP sensor** | **36** | IN pull-up | Sensor → GND when piece detected |
| **MAGNET** | **23** | OUT | Magnet driver input (HIGH = on) |
| **GND** | GND | — | Common with drivers, supplies, switches |
| **3V3 / 5V** | — | — | Logic only; not servo power |

\*Firmware currently uses **FastAccelStepper** (step/dir style). For true MG995 PWM you either:

- use a small step/dir → servo bridge, or  
- retarget the sketch to `Servo` / LEDC PWM on the STEP pins and leave DIR unused.

### Per-joint wiring (one joint shown; repeat for J1/J2/J3)

```
                    ┌─────────────┐
   ESP32 GPIO STEP ─┤ STEP / SIG  │
   ESP32 GPIO DIR  ─┤ DIR         │──► joint actuator (servo or stepper)
   ESP32 GPIO EN   ─┤ ENABLE      │
   ESP32 GND      ─┤ GND         │
                    └──────┬──────┘
                           │
   Servo/driver PSU 5–6 V ─┴── motor power (not from ESP32)
```

### Home / e-stop / pickup (active low to GND)

```
   ESP32 GPIO (HOME / ESTOP / PICKUP)
        │
        ├── internal INPUT_PULLUP
        │
        └── switch/sensor ──► GND   (closed = LOW = asserted)

   Sketch logic:
     HOME  : LOW while switch pressed during homing
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
         12 ────────┼─► J1 STEP/SIG ──► Shoulder MG995*    │
         14 ────────┼─► J1 DIR                             │
         27 ────────┼─► J2 STEP/SIG ──► Elbow MG995*       │
         26 ────────┼─► J2 DIR                             │
         25 ────────┼─► J3 STEP/SIG ──► Wrist MG995*       │
         33 ────────┼─► J3 DIR                             │
         13 ────────┼─► ENABLE (all drivers if shared)     │
                    │                                      │
         32 ────────┼─► J1 home switch                     │
         35 ────────┼─► J2 home switch                     │
         34 ────────┼─► J3 home switch                     │
         39 ────────┼─► E-stop                             │
         36 ────────┼─► Pickup sensor                      │
         23 ────────┼─► Magnet driver                      │
        GND ────────┼─► common ground                      │
                    └──────────────────────────────────────┘

   * or step/dir driver → geared motor; see §4.
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
| Shoulder | GPIO 12 / 14 | same pin numbers on second board |
| Elbow | GPIO 27 / 26 | same |
| Wrist | GPIO 25 / 33 | same |
| Enable | GPIO 13 | same |
| Homes | GPIO 32, 35, 34 | same |
| E-stop | GPIO 39 | same (or shared loop if desired) |
| Pickup | GPIO 36 | same |
| Magnet | GPIO 23 | same |
| Comms | ESP-NOW ↔ gateway | ESP-NOW ↔ gateway |

---

## 8. Bring-up order (electrical)

1. Power **logic only** (gateway + one arm ESP32 on USB). Confirm serial JSON / ESP-NOW.
2. Add **servo PSU** with no load; check 5–6 V under a single servo.
3. Wire **one joint** STEP/DIR (or PWM); command small moves.
4. Wire homes, e-stop, magnet, pickup; verify FAULT on e-stop and pickup telemetry.
5. Repeat for second arm; set MACs; full dual-arm keep-out test.

---

## 9. Related docs

- [Hardware bring-up](hardware.md)
- [Build dimensions](build_dimensions.md)
- [Architecture](architecture.md)
- [Fault recovery](fault_recovery.md)
