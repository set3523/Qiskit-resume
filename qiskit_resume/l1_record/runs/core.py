"""Runs, immutable settings, event log. See docs/CONTRACTS.md section 2.4."""

from __future__ import annotations

import json
import os
import socket
from dataclasses import dataclass
from typing import Any

from qiskit_resume.l0_disk.db import ExperimentDB, utc_now

_DDL = """
CREATE TABLE IF NOT EXISTS runs (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at       TEXT NOT NULL,
    ended_at         TEXT,
    status           TEXT NOT NULL,
    host             TEXT NOT NULL,
    pid              INTEGER NOT NULL,
    code_fingerprint TEXT,
    settings         TEXT NOT NULL,
    env              TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER,
    at     TEXT NOT NULL,
    level  TEXT NOT NULL,
    kind   TEXT NOT NULL,
    detail TEXT
)
"""

_RUN_COLS = "id, started_at, ended_at, status, host, pid, code_fingerprint, settings, env"


@dataclass(frozen=True)
class RunRecord:
    id: int
    started_at: str
    ended_at: str | None
    status: str
    host: str
    pid: int
    code_fingerprint: str | None
    settings: dict[str, Any]
    env: dict[str, Any]


@dataclass(frozen=True)
class EventRecord:
    id: int
    run_id: int | None
    at: str
    level: str
    kind: str
    detail: Any


class ImmutableSettingError(ValueError):
    def __init__(self, key: str, stored: Any, given: Any) -> None:
        self.key, self.stored, self.given = key, stored, given
        super().__init__(
            f"'{key}' is fixed for this experiment file (stored: {stored!r}, given: {given!r}). "
            "Use a new experiment file to change it."
        )


def _run(r: Any) -> RunRecord:
    v = list(r)
    v[7] = json.loads(v[7])
    v[8] = json.loads(v[8])
    return RunRecord(*v)


class RunLog:
    def __init__(self, db: ExperimentDB) -> None:
        db.ensure_schema(_DDL)
        self._db = db

    def start(self, *, code_fingerprint: str | None, settings: dict[str, Any], env: dict[str, Any]) -> RunRecord:
        crashed = [r[0] for r in self._db.conn.execute("SELECT id FROM runs WHERE status='running'")]
        with self._db.tx() as c:
            for rid in crashed:
                c.execute("UPDATE runs SET status='crashed' WHERE id=?", (rid,))
            cur = c.execute(
                f"INSERT INTO runs({_RUN_COLS.replace('id, ', '', 1)}) VALUES (?,?,?,?,?,?,?,?)",
                (utc_now(), None, "running", socket.gethostname(), os.getpid(), code_fingerprint,
                 json.dumps(settings, default=str), json.dumps(env, default=str)),
            )
            run_id = cur.lastrowid
        for rid in crashed:
            self.log_event(run_id, "info", "run_crashed_detected", {"run_id": rid})
        return self.get(run_id)

    def finish(self, run_id: int, status: str) -> None:
        with self._db.tx() as c:
            c.execute("UPDATE runs SET status=?, ended_at=? WHERE id=?", (status, utc_now(), run_id))

    def get(self, run_id: int) -> RunRecord:
        r = self._db.conn.execute(f"SELECT {_RUN_COLS} FROM runs WHERE id=?", (run_id,)).fetchone()
        if r is None:
            raise KeyError(f"run not found: {run_id}")
        return _run(r)

    def previous(self, run_id: int) -> RunRecord | None:
        r = self._db.conn.execute(f"SELECT {_RUN_COLS} FROM runs WHERE id<? ORDER BY id DESC LIMIT 1",
                                  (run_id,)).fetchone()
        return None if r is None else _run(r)

    def list_runs(self) -> list[RunRecord]:
        return [_run(r) for r in self._db.conn.execute(f"SELECT {_RUN_COLS} FROM runs ORDER BY id")]

    def fix_immutable(self, key: str, value: Any) -> Any:
        stored = self._db.get_meta(key)
        if stored is None:
            self._db.set_meta(key, value)
            return value
        if value is not None and stored != value:
            raise ImmutableSettingError(key, stored, value)
        return stored

    def log_event(self, run_id: int | None, level: str, kind: str, detail: Any = None) -> None:
        with self._db.tx() as c:
            c.execute("INSERT INTO events(run_id, at, level, kind, detail) VALUES (?,?,?,?,?)",
                      (run_id, utc_now(), level, kind, None if detail is None else json.dumps(detail, default=str)))

    def events(self, *, run_id: int | None = None, kind: str | None = None) -> list[EventRecord]:
        q, args = "SELECT id, run_id, at, level, kind, detail FROM events WHERE 1=1", []
        if run_id is not None:
            q += " AND run_id=?"
            args.append(run_id)
        if kind is not None:
            q += " AND kind=?"
            args.append(kind)
        out = []
        for r in self._db.conn.execute(q + " ORDER BY id", args):
            v = list(r)
            v[5] = None if v[5] is None else json.loads(v[5])
            out.append(EventRecord(*v))
        return out
