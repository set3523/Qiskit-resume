"""Content-addressed blob store inside the experiment DB (D-013 dedup).

Identical payloads (same circuit, same observable, ...) are stored once and
referenced by their SHA-256 hex digest.
"""

from __future__ import annotations

import hashlib
import sqlite3

from qiskit_resume.l0_disk.db import ExperimentDB


_DDL = """
CREATE TABLE IF NOT EXISTS blobs (
    hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    size INTEGER NOT NULL,
    data BLOB NOT NULL
);
"""


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class BlobStore:
    def __init__(self, db: ExperimentDB) -> None:
        db.ensure_schema(_DDL)
        self._conn = db.conn

    def put(self, data: bytes, *, kind: str = "raw") -> str:
        """Store ``data`` (if new) and return its hash. Caller owns the transaction."""
        h = content_hash(data)
        self._conn.execute(
            "INSERT OR IGNORE INTO blobs(hash, kind, size, data) VALUES (?, ?, ?, ?)",
            (h, kind, len(data), sqlite3.Binary(data)),
        )
        return h

    def get(self, h: str) -> bytes:
        row = self._conn.execute("SELECT data FROM blobs WHERE hash = ?", (h,)).fetchone()
        if row is None:
            raise KeyError(f"blob not found: {h}")
        return bytes(row[0])

    def has(self, h: str) -> bool:
        return self._conn.execute("SELECT 1 FROM blobs WHERE hash = ?", (h,)).fetchone() is not None
