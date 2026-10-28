"""Replay decisions (D-005). See docs/CONTRACTS.md section 3.1.

Decides only; never writes. l4 records what actually happened via the journal.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Literal

from qiskit_resume.l1_record.hashing import InputKey, diff_parts
from qiskit_resume.l1_record.journal import CallRecord, Journal

Action = Literal["replay", "reattach", "execute"]


@dataclass(frozen=True)
class Divergence:
    position: int
    kind: str
    expected: CallRecord
    got: InputKey
    changed_parts: list[str]


@dataclass(frozen=True)
class Decision:
    action: str
    position: int
    occurrence: int
    record: CallRecord | None
    divergence: Divergence | None


class ReplayEngine:
    def __init__(self, journal: Journal, *, run_id: int, start_position: int = 0) -> None:
        self._journal = journal
        self.run_id = run_id
        self.position = 0
        self._occ: Counter[tuple[str, str]] = Counter()
        if start_position:
            self.jump_to(start_position)

    def jump_to(self, position: int) -> None:
        """Continue from ``position`` (snapshot): rebuild occurrence counters from recorded calls before it."""
        self._occ = Counter()
        for rec in self._journal.list_calls():
            if rec.seq < position:
                self._occ[(rec.kind, rec.key_hash)] += 1
        self.position = position

    def next(self, kind: str, key: InputKey) -> Decision:
        pos = self.position
        occ = self._occ[(kind, key.key_hash)]
        self.position += 1
        self._occ[(kind, key.key_hash)] += 1

        rec = self._journal.find(kind, key.key_hash, occ)
        if rec is not None:
            if rec.status == "compacted":
                raise LookupError(
                    f"call #{pos + 1} was removed by `qresume compact`; resume from a snapshot (use_snapshots=True)")
            if rec.status == "done":
                return Decision("replay", pos, occ, rec, None)
            if rec.status == "submitted" and rec.job_id:
                return Decision("reattach", pos, occ, rec, None)
            return Decision("execute", pos, occ, rec, None)

        expected = self._journal.at_position(pos)
        if expected is None:
            return Decision("execute", pos, occ, None, None)
        got_parts = InputKey(expected.key_hash, expected.parts)
        changed = diff_parts(got_parts, key) if expected.kind == kind else ["kind"]
        return Decision("execute", pos, occ, None, Divergence(pos, kind, expected, key, changed))

    def has_recorded_after(self, position: int) -> bool:
        m = self._journal.max_seq()
        return m is not None and m >= position
