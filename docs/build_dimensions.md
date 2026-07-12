# Build dimensions (matches current software)

Coordinate frame used in code: origin at **table center**, **+X right**, **+Y toward Black’s side of the table**, mm.

Source of truth in software: `chess_robot/config.py`, `chess_robot/geometry.py`, `chess_robot/trajectory.py`.

---

## 1. Table / grid

| Item | Dimension |
|------|-----------|
| Piece cell size | **50 × 50 mm** |
| Empty separators | **20 mm** wide (between rack and board) |
| Piece columns | **12** (C1…C12 among 50 mm cells only) |
| Full table work surface | **640 × 400 mm** |
| Table left edge | **x = −320** |
| Table right edge | **x = +320** |
| Table bottom edge (White side) | **y = −200** |
| Table top edge (Black side) | **y = +200** |

### Column map (left → right)

| Region | Role | Labels / size |
|--------|------|----------------|
| **C1–C2** | White dead rack | **W1…W16**, 50 mm cells |
| gap | Empty separator | **20 mm** (no pieces) |
| **C3–C10** | Chessboard | **a1…h8**, 50 mm cells |
| gap | Empty separator | **20 mm** (no pieces) |
| **C11–C12** | Black dead rack | **B1…B16**, 50 mm cells |

### Rows (bottom → top)

| Rows | Labels |
|------|--------|
| **R1…R8** | same sense as chess ranks 1…8 |

### Chess play area (physical square centers)

| Square | Center (x, y) mm |
|--------|------------------|
| **a1** | (−175, −175) |
| **h1** | (+175, −175) |
| **a8** | (−175, +175) |
| **h8** | (+175, +175) |
| Board outer extent (play area) | **400 × 400 mm** (8×8 × 50) |
| Board left edge | **x = −200** (start of file a) |
| Board right edge | **x = +200** (end of file h) |

Chessboard is **centered in X** on the 640 mm table (100 mm rack + 20 mm gap each side).

### Dead racks (piece centers)

Fill order: **top → bottom**, two columns, left→right in each row.

**White (C1–C2):** W1 at (−295, +175) … W16 at (−245, −175)

**Black (C11–C12):** B1 at (+245, +175) … B16 at (+295, −175)

### Recommended physical build size

| Piece | Size |
|-------|------|
| Magnetic play surface | at least **640 × 400 mm** |
| Frame / clearance around | leave room for bases **50 mm** outside long edges and arm swing |
| Overall footprint (rough) | plan ~**750 × 600+ mm** free for bases + rest poses |

---

## 2. Arm bases (critical)

| Arm | Base center (x, y) mm | Notes |
|-----|----------------------|--------|
| **White** | **(0, −255)** | **55 mm** outside bottom table edge (edge is y = −200) |
| **Black** | **(0, +255)** | **55 mm** outside top table edge (edge is y = +200) |

Both bases are on the **centerline** of the table in X (x = 0).

**Base setback:** **55 mm** from the outer edge of the first grid row to the **rotation center of joint 1**.

| Arm | Base “forward” heading (local 0°) |
|-----|-----------------------------------|
| White | **+45°** from world +X |
| Black | **−135°** from world +X (180° opposite of White) |

Local 0° = “arm fully straight when J1 = J2 = J3 = 0”.

---

## 3. Link lengths (unequal 3R)

| Link | Length | Joint it leaves |
|------|--------|-----------------|
| **L1** | **200 mm** | Base / shoulder (J1) → elbow (J2) |
| **L2** | **160 mm** | Elbow (J2) → wrist (J3) |
| **L3** | **180 mm** | Wrist (J3) → magnet center |
| **Max reach** | **540 mm** | L1 + L2 + L3 fully straight |

Measure **joint axis → joint axis** (and last axis → magnet center), not outer plastic length.

Suggested mechanical stack per joint:

- Servo body + horn + link arm
- Keep **planar** (all rotation axes vertical for XY motion)
- Fixed tool height (no Z lift in software)

---

## 4. Joint travel (MG995 180°)

Software motor windows:

| Joint | Range | Meaning |
|-------|-------|---------|
| **J1 shoulder** | **−90° … +90°** | 0° = along local forward |
| **J2 elbow** | **0° … +180°** | 0° ≈ open/straight, 180° = fold |
| **J3 wrist** | **0° … +180°** | same idea as elbow |

**Calibrate so:**

| Pose | J1 | J2 | J3 |
|------|----|----|-----|
| **Fully extended (straight)** | **0°** | **0°** | **0°** |
| **Outside rest (park)** | **−45°** | **0°** | **90°** |

Outside rest (keep-out home): **L1+L2 straight** along the long exterior edge; **wrist 90°** bends L3 around the short exterior so arms stay off the board and do not cross each other.

| Arm | Elbow (end L1) | Wrist (end L2) | Tool | Corridor |
|-----|----------------|----------------|------|----------|
| White | (200, −255) | (360, −255) | **(360, −75)** | bottom → right outside |
| Black | (−200, +255) | (−360, +255) | **(−360, +75)** | top → left outside |

Leave ~**5°** software margin inside hard stops for normal IK motion (home is an explicit pose; elbow 0° at rest is intentional).

---

## 5. Pieces / magnet path

| Item | Spec |
|------|------|
| Puck diameter (planner) | **30 mm** |
| Clearance between pucks | **2 mm** (planner keeps centers ≥ **32 mm** apart on path) |
| Suggested cell magnet | weak snap under **cell center** |
| Arm electromagnet | on tool; dwell **0.5 s** pickup and **0.5 s** release |
| Tool height | **fixed** (one Z for whole table) — software `fixed_tool_z_mm = 0` is logical; physical Z is your design |

---

## 6. Software staging points (optional physical marks)

Not mandatory as hardware, but IK expects these if used:

| Name | (x, y) mm |
|------|-----------|
| White buffer | **(−370, 0)** — 50 mm left of table |
| Black buffer | **(+370, 0)** — 50 mm right of table |
| Park (logical) | same as base XY; actual folded tool is offset along base line |

---

## 7. One-page sketch (top view)

```
                    Black base (0, +255)
                           ●
                    ← 50 mm →
  y=+200 ┌────┬┬──────────────────┬┬────┐
         │ W  ││     a8 … h8      ││ B  │
         │rack││     (400×400)    ││rack│
  y=0    │C1-2││     C3 … C10     ││C11-12
         │    ││     a1 … h1      ││    │
  y=-200 └────┴┴──────────────────┴┴────┘
         x=-320   20mm    x=0   20mm  x=+320
                    ← 50 mm →
                           ●
                    White base (0, −255)
```

White rack **C1–C2**, **20 mm** gap, chess **C3–C10**, **20 mm** gap, Black rack **C11–C12**.

---

## 8. Build checklist (what to hit exactly)

1. **Table:** **640 × 400 mm** — 50 mm piece cells + **20 mm** live/dead gaps
2. **Chess area:** centers from **(−175, −175)** to **(+175, +175)** (still centered)
3. **Bases:** **(0, ±255)** (55 mm off first grid row edge), rotation axes vertical
4. **Links:** **200 / 160 / 180 mm** axis-to-axis
5. **Servo zero:** **0 / 0 / 0** = fully straight along **+45°** (White) / **−135°** (Black)
6. **Outside rest:** **−45 / 0 / 90** — L1+L2 straight on long exterior; L3 bends around short exterior
7. **Pucks ~30 mm** diameter, steel insert; weak cell magnets at centers
8. **Keep arms planar**; one fixed working height for the magnet face

---

## 9. Not fixed by software (your choice)

- Frame height / magnet gap above board
- Servo brand mounting, gear ratio, horn clocking (set zero after assembly)
- Cable routing, power supply (servos need a solid 5–6 V high-current supply)
- Exact puck height / felt / board material

---

## Related docs

- [Hardware bring-up](hardware.md)
- [Architecture](architecture.md)
- [Fault recovery](fault_recovery.md)
