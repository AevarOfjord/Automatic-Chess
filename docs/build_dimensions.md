# Build dimensions (matches current software)

Coordinate frame used in code: origin at **table center**, **+X right**, **+Y toward Black’s side of the table**, mm.

Source of truth in software: `chess_robot/config.py`, `chess_robot/geometry.py`, `chess_robot/trajectory.py`.

---

## 1. Table / grid

| Item | Dimension |
|------|-----------|
| Cell size | **50 × 50 mm** |
| Grid | **14 columns × 8 rows** |
| Full table work surface | **700 × 400 mm** |
| Table left edge | **x = −350** |
| Table right edge | **x = +350** |
| Table bottom edge (White side) | **y = −200** |
| Table top edge (Black side) | **y = +200** |

### Column map (left → right)

| Columns | Role | Labels |
|---------|------|--------|
| **C1–C2** | White dead rack | **W1…W16** |
| **C3** | Empty separator | (no pieces) |
| **C4–C11** | Chessboard | **a1…h8** |
| **C12** | Empty separator | (no pieces) |
| **C13–C14** | Black dead rack | **B1…B16** |

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

Chessboard is **centered in X** on the 600 mm table (100 mm rack each side).

### Dead racks (piece centers)

Fill order: **top → bottom**, two columns, left→right in each row.

**White (C1–C2):** W1 at (−325, +175) … W16 at (−275, −175)

**Black (C13–C14):** B1 at (+275, +175) … B16 at (+325, −175)

### Recommended physical build size

| Piece | Size |
|-------|------|
| Magnetic play surface | at least **700 × 400 mm** |
| Frame / clearance around | leave room for bases **50 mm** outside long edges and arm swing |
| Overall footprint (rough) | plan ~**800 × 600+ mm** free for bases + folded arms |

---

## 2. Arm bases (critical)

| Arm | Base center (x, y) mm | Notes |
|-----|----------------------|--------|
| **White** | **(0, −250)** | **50 mm** outside bottom table edge (edge is y = −200) |
| **Black** | **(0, +250)** | **50 mm** outside top table edge (edge is y = +200) |

Both bases are on the **centerline** of the table in X (x = 0).

**Base setback:** **50 mm** from the long table edge to the **rotation center of joint 1**.

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
| **Folded rest (park)** | **−45°** | **180°** | **180°** |

Folded rest is **parallel to the long table edge** (world ±X): White tool near **(220, −250)**, Black near **(−220, +250)**.

Leave ~**5°** software margin inside the hard stops (don’t ride mechanical limits).

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
| White buffer | **(−400, 0)** — 50 mm left of table |
| Black buffer | **(+400, 0)** — 50 mm right of table |
| Park (logical) | same as base XY; actual folded tool is offset along base line |

---

## 7. One-page sketch (top view)

```
                    Black base (0, +250)
                           ●
                    ← 50 mm →
  y=+200 ┌────┬─┬──────────────────┬─┬────┐
         │ W  │ │     a8 … h8      │ │ B  │
         │rack│g│     (400×400)    │g│rack│
  y=0    │C1-2│3│     C4 … C11     │12│13-14
         │    │ │     a1 … h1      │ │    │
  y=-200 └────┴─┴──────────────────┴─┴────┘
         x=-350        x=0          x=+350
                    ← 50 mm →
                           ●
                    White base (0, −250)
```

White rack **C1–C2**, empty **C3**, chess **C4–C11**, empty **C12**, Black rack **C13–C14**.

---

## 8. Build checklist (what to hit exactly)

1. **Grid:** 14×8 cells @ **50 mm**, total **700×400 mm**
2. **Chess area:** centers from **(−175, −175)** to **(+175, +175)** (still centered)
3. **Bases:** **(0, ±250)**, rotation axes vertical
4. **Links:** **200 / 160 / 180 mm** axis-to-axis
5. **Servo zero:** **0 / 0 / 0** = fully straight along **+45°** (White) / **−135°** (Black)
6. **Fold:** **−45 / 180 / 180** — parallel to the long table edge, outside the board
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
