"""run.snapshot(state) / run.load_snapshot() (D-011, D-012). See docs/CONTRACTS.md section 5.7.

Takes the ResumeRun object duck-typed (no import of l4_api.run, to keep the
dependency direction run -> snapshot).
"""

from __future__ import annotations

from typing import Any

from qiskit_resume.l0_disk.codec import decode, encode
from qiskit_resume.l4_api.rng import capture_state, restore_state


def save(run: Any, state: dict[str, Any]) -> int:
    if not isinstance(state, dict):
        raise TypeError("snapshot state must be a dict")
    data = encode(state)  # fails fast with the path of an unstorable value
    rec = run.snaps.save(run_id=run.run_id, position=run.engine.position, state=data, rng=capture_state())
    return rec.id


def load(run: Any) -> dict[str, Any] | None:
    if not run.settings.get("use_snapshots", True):
        return None
    max_seq = run.journal.max_seq()
    if max_seq is None:
        return None
    # only snapshots that still have recorded calls after them, so the first call can be verified (D-012)
    snap = run.snaps.latest_valid(before_position=max_seq + 1)
    if snap is None or snap.position <= run.engine.position:
        return None
    state, rng_state = run.snaps.load(snap)
    run.engine.jump_to(snap.position)
    if rng_state is not None:
        restore_state(rng_state)
    run._loaded_snapshot = snap
    run._snapshot_guard_until = max_seq  # every replayed call up to here also verifies the snapshot
    run.runs.log_event(run.run_id, "info", "snapshot_loaded", {"snapshot_id": snap.id, "position": snap.position})
    run._say("info", f"resuming from snapshot #{snap.id} (call #{snap.position}); calls before it are skipped")
    return decode(state)
