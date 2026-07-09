# Fault recovery playbook

When the PC raises a fault, motion stops. Do **not** clear the fault until the physical table matches the logical board.

## Common fault sources

| Symptom | Likely cause | Operator action |
|---------|--------------|-----------------|
| `gateway timeout` | Serial disconnect, wrong port, arm powered off | Check USB, COM port, power; PC retries timeouts once by default |
| `pickup sensor did not detect a piece` | Missed grab, empty square, sensor wiring | Inspect magnet, puck, sensor; re-seat puck; home and retry |
| `camera mismatch` | Piece slid, wrong square, lighting, stale calibration | Align pieces to logical board; recalibrate if needed |
| `cannot plan puck path` | Crowded geometry / clearance too tight | Free blocked corridor; inspect trajectory clearance settings |
| `cannot reach …` | Kinematics / park / base mis-config | Run reachability; verify `config.py` against table |
| `system is faulted` | Previous unrecovered fault | Complete checklist below before `clear_fault_after_manual_inspection` |

## Operator checklist

1. **Stop** — confirm both arms are stopped and magnets off.
2. **Read** the fault string (`GameManager.last_fault` or console / journal).
3. **Inspect** the table:
   - Every occupied square matches the PC board position.
   - Dead-piece slots match inventory (`W1…` / `B1…` order).
   - No pucks left on buffers or under tools.
4. **Restore** any displaced puck to the square/slot the PC expects.
5. **Home** both arms (manual or via software home).
6. **Vision check** — overhead occupancy must match `board.piece_map()` squares.
7. **Clear fault** only when camera and logical board agree:

   ```python
   manager.clear_fault_after_manual_inspection()
   ```

8. Resume play, or run a full **reset plan** if the position is the start position.

## What the software will not do automatically

- Guess piece **identity** from the camera (occupancy only).
- Rewrite inventory from a messy table without operator help.
- Parallel dual-arm work after a fault (keep-out remains serialized).

## Journal

Commands and responses append to `runtime_data/command_journal.jsonl`.  
When the file exceeds ~5 MB it rotates to `command_journal.jsonl.1`.  
Use the journal to see the last successful transfer before a fault.

## Prevention habits

- Run visual + unit tests after any planner or geometry change.
- Keep one arm parked while the other transfers (enforced in software).
- Recalibrate the camera after moving the mount or changing lighting.
- Prefer mock simulation overnight; physical unattended loops only after supervised games are reliable.
