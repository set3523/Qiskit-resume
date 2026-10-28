"""What to do when the replayed question differs from the record (D-006, D-012, D-014).

See docs/CONTRACTS.md section 3.2.
"""

from __future__ import annotations

from dataclasses import dataclass

from qiskit_resume.l1_record.journal import Journal
from qiskit_resume.l1_record.runs import RunLog
from qiskit_resume.l2_replay.replay import Divergence


@dataclass(frozen=True)
class Verdict:
    level: str
    message: str
    action: str
    invalidate_from: int | None


def _where(div: Divergence, location: str | None = None) -> str:
    parts = ", ".join(div.changed_parts) or "?"
    at = f" (made at {location})" if location else ""
    return f"call #{div.position + 1} ({div.kind}){at} differs from the record in: {parts}"


def decide(div: Divergence, *, code_changed: bool, started_from_snapshot: bool, force: bool,
           on_divergence: str = "warn", location: str | None = None) -> Verdict:
    where = _where(div, location)
    if force:
        return Verdict("info", f"{where}. force=True: branching here; later records are kept as invalid.",
                       "continue", div.position)
    if list(div.changed_parts) == ["backend"]:  # only the target device differs (e.g. a transpile)
        stop = on_divergence == "stop"
        msg = (f"{where}: the device changed since this call was recorded, so it is redone for the new device. "
               + ("Stopping because on_divergence='stop'." if stop
                  else "Continuing with real execution from here (later calls use QPU time)."))
        return Verdict("warning", msg, "stop" if stop else "continue", None if stop else div.position)
    if code_changed:
        stop = on_divergence == "stop"
        msg = (f"{where}. Your script (or its arguments) changed since the last run, so this is treated as intentional. "
               + ("Stopping because on_divergence='stop'." if stop
                  else "Continuing with real execution from here (uses QPU time)."))
        return Verdict("warning", msg, "stop" if stop else "continue", None if stop else div.position)
    if started_from_snapshot:
        return Verdict(
            "error",
            f"{where}. Resuming from the snapshot is not possible: the snapshot is missing some state "
            "(your code did not change). The snapshot was marked invalid - run again to replay from an "
            "earlier point (no QPU time used). If this change is intentional, run with force=True.",
            "stop", None)
    stop = on_divergence == "stop"
    msg = (f"{where}, although your script did not change. Likely unseeded randomness used before this "
           "call (e.g. np.random.default_rng() or np.random.rand() without a seed): replace it with "
           "resume.rng(\"name\"), or resume.seed_for(\"name\") where an int seed is needed. "
           + ("Stopping because on_divergence='stop'." if stop
              else "Continuing with real execution from here (uses QPU time)."))
    return Verdict("warning", msg, "stop" if stop else "continue", None if stop else div.position)


def apply(verdict: Verdict, div: Divergence, *, journal: Journal, runs: RunLog, run_id: int) -> list[int]:
    runs.log_event(run_id, verdict.level, "force_branch" if verdict.level == "info" else "divergence",
                   {"position": div.position, "kind": div.kind, "changed_parts": div.changed_parts,
                    "action": verdict.action})
    if verdict.action != "continue" or verdict.invalidate_from is None:
        return []
    ids = journal.invalidate_from(verdict.invalidate_from)
    if ids:
        runs.log_event(run_id, "info", "calls_invalidated", {"from_seq": verdict.invalidate_from, "ids": ids})
    return ids
