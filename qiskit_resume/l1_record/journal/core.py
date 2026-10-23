"""Call journal (CallRecord). See docs/CONTRACTS.md section 2.2.

Storage only - no decisions. Each method is its own atomic transaction, so
call these *outside* any ``db.tx()`` block.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Literal

from qiskit_resume.l0_disk.blobs import BlobStore
from qiskit_resume.l0_disk.db import ExperimentDB, utc_now

CallKind = Literal["estimator", "sampler", "transpile", "function"]
CallStatus = Literal["submitted", "done", "failed", "invalid", "compacted"]

_DDL = """
CREATE TABLE IF NOT EXISTS calls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    seq         INTEGER NOT NULL,
    kind        TEXT NOT NULL,
    key_hash    TEXT NOT NULL,
    occurrence  INTEGER NOT NULL,
    parts       TEXT NOT NULL,
    status      TEXT NOT NULL,
    job_id      TEXT,
    backend     TEXT,
    inputs_ref  TEXT,
    result_ref  TEXT,
    usage_s     REAL,
    duration_s  REAL,
    created_at  TEXT NOT NULL,
    finished_at TEXT,
    error       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS calls_live_key ON calls(kind, key_hash, occurrence) WHERE status != 'invalid';
CREATE INDEX IF NOT EXISTS calls_seq ON calls(seq);
CREATE INDEX IF NOT EXISTS calls_job ON calls(job_id)
"""

_COLS = (
    "id, run_id, seq, kind, key_hash, occurrence, parts, status, job_id, backend, inputs_ref, "
    "result_ref, usage_s, duration_s, created_at, finished_at, error"
)


@dataclass(frozen=True)
class CallRecord:
    id: int
    run_id: int
    seq: int
    kind: str
    key_hash: str
    occurrence: int
    parts: dict[str, str]
    status: str
    job_id: str | None
    backend: str | None
    inputs_ref: str | None
    result_ref: str | None
    usage_s: float | None
    duration_s: float | None
    created_at: str
    finished_at: str | None
    error: str | None


def _row(r: Any) -> CallRecord:
    vals = list(r)
    vals[6] = json.loads(vals[6])
    return CallRecord(*vals)


class Journal:
    def __init__(self, db: ExperimentDB, blobs: BlobStore) -> None:
        db.ensure_schema(_DDL)
        self._db = db
        self._blobs = blobs

    # -- writes ------------------------------------------------------------------

    def record_submitted(
        self,
        *,
        run_id: int,
        seq: int,
        kind: str,
        key: Any,
        occurrence: int,
        job_id: str | None = None,
        backend: str | None = None,
        inputs_ref: str | None = None,
    ) -> CallRecord:
        with self._db.tx() as c:
            cur = c.execute(
                "INSERT INTO calls(run_id, seq, kind, key_hash, occurrence, parts, status, job_id, backend,"
                " inputs_ref, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (run_id, seq, kind, key.key_hash, occurrence, json.dumps(dict(key.parts)), "submitted",
                 job_id, backend, inputs_ref, utc_now()),
            )
            call_id = cur.lastrowid
        return self.get(call_id)

    def set_job(self, call_id: int, job_id: str, backend: str | None = None) -> CallRecord:
        rec = self.get(call_id)
        if rec.status != "submitted":
            raise ValueError(f"call {call_id} is {rec.status}, cannot attach a job")
        with self._db.tx() as c:
            c.execute("UPDATE calls SET job_id = ?, backend = COALESCE(?, backend) WHERE id = ?",
                      (job_id, backend, call_id))
        return self.get(call_id)

    def record_done(self, call_id: int, *, result_ref: str, usage_s: float | None = None,
                    duration_s: float | None = None) -> CallRecord:
        if not self._blobs.has(result_ref):
            raise KeyError(f"result blob not stored: {result_ref}")
        self._transition(call_id, "done")
        with self._db.tx() as c:
            c.execute("UPDATE calls SET status='done', result_ref=?, usage_s=?, duration_s=?, finished_at=?,"
                      " error=NULL WHERE id=?", (result_ref, usage_s, duration_s, utc_now(), call_id))
        return self.get(call_id)

    def record_failed(self, call_id: int, *, error: str) -> CallRecord:
        self._transition(call_id, "failed")
        with self._db.tx() as c:
            c.execute("UPDATE calls SET status='failed', error=?, finished_at=? WHERE id=?",
                      (error, utc_now(), call_id))
        return self.get(call_id)

    def set_usage(self, call_id: int, usage_s: float) -> CallRecord:
        with self._db.tx() as c:
            c.execute("UPDATE calls SET usage_s=? WHERE id=?", (usage_s, call_id))
        return self.get(call_id)

    def invalidate(self, call_ids: Iterable[int]) -> list[int]:
        ids = [int(i) for i in call_ids]
        if ids:
            with self._db.tx() as c:
                c.executemany("UPDATE calls SET status='invalid' WHERE id=?", [(i,) for i in ids])
        return ids

    def invalidate_from(self, seq: int, *, reason: str = "") -> list[int]:
        ids = [r[0] for r in self._db.conn.execute(
            "SELECT id FROM calls WHERE seq >= ? AND status != 'invalid' ORDER BY id", (seq,))]
        return self.invalidate(ids)

    def _transition(self, call_id: int, to: str) -> None:
        rec = self.get(call_id)
        if rec.status != "submitted":
            raise ValueError(f"call {call_id}: illegal transition {rec.status} -> {to}")

    # -- reads -------------------------------------------------------------------

    def get(self, call_id: int) -> CallRecord:
        r = self._db.conn.execute(f"SELECT {_COLS} FROM calls WHERE id=?", (call_id,)).fetchone()
        if r is None:
            raise KeyError(f"call not found: {call_id}")
        return _row(r)

    def find(self, kind: str, key_hash: str, occurrence: int) -> CallRecord | None:
        r = self._db.conn.execute(
            f"SELECT {_COLS} FROM calls WHERE kind=? AND key_hash=? AND occurrence=? AND status!='invalid'",
            (kind, key_hash, occurrence)).fetchone()
        return None if r is None else _row(r)

    def at_position(self, seq: int) -> CallRecord | None:
        r = self._db.conn.execute(
            f"SELECT {_COLS} FROM calls WHERE seq=? AND status!='invalid' ORDER BY id DESC LIMIT 1",
            (seq,)).fetchone()
        return None if r is None else _row(r)

    def list_calls(self, *, include_invalid: bool = False) -> list[CallRecord]:
        where = "" if include_invalid else "WHERE status != 'invalid'"
        return [_row(r) for r in self._db.conn.execute(
            f"SELECT {_COLS} FROM calls {where} ORDER BY seq, id")]

    def max_seq(self) -> int | None:
        r = self._db.conn.execute("SELECT MAX(seq) FROM calls WHERE status != 'invalid'").fetchone()
        return r[0]
