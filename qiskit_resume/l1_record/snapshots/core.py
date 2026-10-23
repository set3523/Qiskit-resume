"""Snapshot records (D-011). See docs/CONTRACTS.md section 2.3."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from qiskit_resume.l0_disk.blobs import BlobStore
from qiskit_resume.l0_disk.db import ExperimentDB, utc_now

_DDL = """
CREATE TABLE IF NOT EXISTS snapshots (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id     INTEGER NOT NULL,
    position   INTEGER NOT NULL,
    state_ref  TEXT NOT NULL,
    rng_ref    TEXT,
    created_at TEXT NOT NULL,
    valid      INTEGER NOT NULL DEFAULT 1
)
"""
_COLS = "id, run_id, position, state_ref, rng_ref, created_at, valid"


@dataclass(frozen=True)
class SnapshotRecord:
    id: int
    run_id: int
    position: int
    state_ref: str
    rng_ref: str | None
    created_at: str
    valid: bool


def _row(r: Any) -> SnapshotRecord:
    v = list(r)
    v[6] = bool(v[6])
    return SnapshotRecord(*v)


class SnapshotStore:
    def __init__(self, db: ExperimentDB, blobs: BlobStore) -> None:
        db.ensure_schema(_DDL)
        self._db, self._blobs = db, blobs

    def save(self, *, run_id: int, position: int, state: bytes, rng: bytes | None) -> SnapshotRecord:
        with self._db.tx() as c:
            sref = self._blobs.put(state, kind="state")
            rref = self._blobs.put(rng, kind="rng") if rng is not None else None
            cur = c.execute(f"INSERT INTO snapshots({_COLS.replace('id, ', '', 1)}) VALUES (?,?,?,?,?,1)",
                            (run_id, position, sref, rref, utc_now()))
            sid = cur.lastrowid
        return self.get(sid)

    def get(self, snap_id: int) -> SnapshotRecord:
        r = self._db.conn.execute(f"SELECT {_COLS} FROM snapshots WHERE id=?", (snap_id,)).fetchone()
        if r is None:
            raise KeyError(f"snapshot not found: {snap_id}")
        return _row(r)

    def latest_valid(self, *, before_position: int | None = None) -> SnapshotRecord | None:
        q, args = f"SELECT {_COLS} FROM snapshots WHERE valid=1", []
        if before_position is not None:
            q += " AND position < ?"
            args.append(before_position)
        r = self._db.conn.execute(q + " ORDER BY position DESC, id DESC LIMIT 1", args).fetchone()
        return None if r is None else _row(r)

    def load(self, snap: SnapshotRecord) -> tuple[bytes, bytes | None]:
        return self._blobs.get(snap.state_ref), (self._blobs.get(snap.rng_ref) if snap.rng_ref else None)

    def invalidate(self, snap_id: int) -> None:
        with self._db.tx() as c:
            c.execute("UPDATE snapshots SET valid=0 WHERE id=?", (snap_id,))

    def list_snapshots(self, *, include_invalid: bool = False) -> list[SnapshotRecord]:
        where = "" if include_invalid else "WHERE valid=1"
        return [_row(r) for r in self._db.conn.execute(f"SELECT {_COLS} FROM snapshots {where} ORDER BY id")]
