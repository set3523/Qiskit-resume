"""The ``.qresume`` experiment file: one SQLite database per experiment (D-004).

Responsibilities (no Qiskit knowledge here):

* schema creation / format-version check
* durable transactions (each record is committed atomically; a kill or power
  loss can never leave a half-written record)
* single-writer guarantee per experiment file (D-014): an OS-level lock on a
  sidecar ``<file>.lock``; the OS releases it automatically if the process
  dies, so there are no stale locks (B4)
* concurrent read-only access while a run is writing (``status``/``check``)
* consistent online copy via the SQLite backup API (``qresume copy``, D-012)
* nothing is ever deleted or compacted while running (D-014)
"""

from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import socket
import sqlite3
import sys
import threading
from pathlib import Path
from typing import Any, Iterator

FORMAT_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class AlreadyRunningError(RuntimeError):
    """Another process holds the write lock for this experiment file."""


class IntegrityError(RuntimeError):
    """The experiment file is damaged (e.g. copied while being written)."""


class FormatError(RuntimeError):
    """The file was written by a newer, unsupported format version."""


# ---------------------------------------------------------------------------
# OS-level single-writer lock (released by the OS when the process dies)
# ---------------------------------------------------------------------------

_LOCK_OFFSET = 1 << 20  # lock a byte far past the info text so the text stays readable


class _RunLock:
    def __init__(self, lock_path: Path) -> None:
        self.path = lock_path
        self._fh: Any = None

    def acquire(self, info: dict[str, Any]) -> None:
        fh = open(self.path, "a+b")
        try:
            if sys.platform == "win32":
                import msvcrt

                fh.seek(_LOCK_OFFSET)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            fh.close()
            raise AlreadyRunningError(self._holder_message()) from None
        fh.seek(0)
        fh.truncate(0)
        fh.write(json.dumps(info).encode("utf-8"))
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._fh.seek(_LOCK_OFFSET)
                with contextlib.suppress(OSError):
                    msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            # The lock file is left in place on purpose: deleting it could let two
            # processes lock different inodes of the "same" file (POSIX race).
            self._fh.close()
            self._fh = None

    def _holder_message(self) -> str:
        try:
            with open(self.path, "rb") as fh:
                info = json.loads(fh.read(4096).split(b"\x00", 1)[0] or b"{}")
        except (OSError, ValueError):
            info = {}
        who = ", ".join(f"{k}: {info[k]}" for k in ("host", "pid", "started_at") if k in info)
        return f"this experiment file is already being run by another process ({who or 'unknown'})"


def lock_path_for(db_path: Path) -> Path:
    return db_path.with_name(db_path.name + ".lock")


# ---------------------------------------------------------------------------
# Experiment DB
# ---------------------------------------------------------------------------


class ExperimentDB:
    """Open with :meth:`open_for_run` (single writer) or :meth:`open_read_only`."""

    def __init__(self, path: Path, conn: sqlite3.Connection, lock: _RunLock | None, read_only: bool) -> None:
        self.path = path
        self.conn = conn
        self.read_only = read_only
        self._lock = lock
        self.journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        # Libraries (e.g. qiskit_algorithms) call primitives from worker threads. The connection may be
        # used from any thread; this lock serializes every transaction (and callers can hold it longer).
        self.lock = threading.RLock()

    # -- opening -------------------------------------------------------------

    @classmethod
    def open_for_run(cls, path: str | os.PathLike[str]) -> "ExperimentDB":
        p = Path(path)
        if p.parent and not p.parent.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
        lock = _RunLock(lock_path_for(p))
        lock.acquire({"host": socket.gethostname(), "pid": os.getpid(), "started_at": utc_now()})
        try:
            conn = sqlite3.connect(str(p), isolation_level=None, timeout=30.0, check_same_thread=False)
            _configure_writer(conn)
            conn.executescript(_SCHEMA)
            db = cls(p, conn, lock, read_only=False)
            db._init_meta()
            return db
        except BaseException:
            lock.release()
            raise

    @classmethod
    def open_read_only(cls, path: str | os.PathLike[str]) -> "ExperimentDB":
        p = Path(path)
        if not p.is_file():
            raise FileNotFoundError(str(p))
        # Plain path + query_only instead of a "file:...?mode=ro" URI: URIs reject Windows network
        # paths (\\server\share -> "invalid uri authority").
        conn = sqlite3.connect(str(p), isolation_level=None, timeout=30.0, check_same_thread=False)
        conn.execute("PRAGMA query_only=ON")
        db = cls(p, conn, None, read_only=True)
        db._check_format()
        return db

    def close(self) -> None:
        try:
            self.conn.close()
        finally:
            if self._lock is not None:
                self._lock.release()
                self._lock = None

    def __enter__(self) -> "ExperimentDB":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transactions ----------------------------------------------------------

    @contextlib.contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        """One atomic, durable write. Everything inside commits together or not at all."""
        if self.read_only:
            raise RuntimeError("experiment opened read-only")
        with self.lock:
            self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                self.conn.execute("ROLLBACK")
                raise
            else:
                self.conn.execute("COMMIT")

    def ensure_schema(self, ddl: str) -> None:
        """Create tables owned by a higher-layer module (idempotent ``CREATE ... IF NOT EXISTS``).

        l0 owns only ``meta`` and ``blobs``; each l1 module brings its own DDL so the
        table layout of a record type lives next to the code that writes it.
        """
        if self.read_only:
            return
        # execute() (not executescript) so an open transaction is not committed early
        for stmt in ddl.split(";"):
            if stmt.strip():
                self.conn.execute(stmt)

    # -- meta ------------------------------------------------------------------

    def get_meta(self, key: str, default: Any = None) -> Any:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return default if row is None else json.loads(row[0])

    def set_meta(self, key: str, value: Any) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO meta(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, json.dumps(value)),
            )

    def _init_meta(self) -> None:
        existing = self.get_meta("format_version")
        if existing is None:
            with self.tx() as c:
                for k, v in (
                    ("format_version", FORMAT_VERSION),
                    ("created_at", utc_now()),
                    ("package", "qiskit-resume"),
                ):
                    c.execute("INSERT INTO meta(key, value) VALUES (?, ?)", (k, json.dumps(v)))
        else:
            self._check_format()

    def _check_format(self) -> None:
        v = self.get_meta("format_version")
        if v is None:
            raise IntegrityError(f"{self.path} is not a qiskit-resume experiment file")
        if int(v) > FORMAT_VERSION:
            raise FormatError(
                f"{self.path} uses format version {v}, newer than this qiskit-resume supports "
                f"({FORMAT_VERSION}); upgrade qiskit-resume"
            )

    # -- integrity -------------------------------------------------------------

    def integrity_check(self) -> list[str]:
        rows = [r[0] for r in self.conn.execute("PRAGMA integrity_check").fetchall()]
        return [] if rows == ["ok"] else rows


def _configure_writer(conn: sqlite3.Connection) -> None:
    # One mode everywhere (local disk, network share, USB): the classic rollback journal.
    # WAL would be faster but needs shared memory that network drives do not provide safely,
    # and our write rate (one small transaction per quantum call) does not need it.
    # Readers (status/check/copy) still work during a run: each write holds the lock for
    # milliseconds and readers wait up to the 30 s busy timeout.
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("PRAGMA synchronous=FULL")  # durable even on power loss (D-004)
    conn.execute("PRAGMA foreign_keys=ON")


def backup_copy(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> Path:
    """Consistent copy of an experiment file, safe while a run is writing to it (D-012)."""
    dst_p = Path(dst)
    if dst_p.exists():
        raise FileExistsError(str(dst_p))
    with contextlib.closing(ExperimentDB.open_read_only(src)) as source:
        target = sqlite3.connect(str(dst_p))
        try:
            source.conn.backup(target)
        finally:
            target.close()
    return dst_p


def verify_file(path: str | os.PathLike[str]) -> None:
    """Raise :class:`IntegrityError` if the file is damaged (e.g. raw-copied mid-write)."""
    try:
        db = ExperimentDB.open_read_only(path)
    except sqlite3.DatabaseError as exc:
        raise IntegrityError(
            f"{path} is damaged ({exc}). If it was copied while running, use `qresume copy` instead."
        ) from exc
    try:
        problems = db.integrity_check()
    except sqlite3.DatabaseError as exc:
        problems = [str(exc)]
    finally:
        db.close()
    if problems:
        raise IntegrityError(
            f"{path} is damaged: {problems[:3]}. If it was copied while running, use `qresume copy` instead."
        )


def running_info(path: str | os.PathLike) -> dict[str, Any] | None:
    """If a process is running this experiment file, its {host, pid, started_at}; else None."""
    lock = _RunLock(lock_path_for(Path(path)))
    try:
        lock.acquire({"probe": True})
    except AlreadyRunningError:
        try:
            with open(lock.path, "rb") as fh:
                return json.loads(fh.read(4096).split(b"\x00", 1)[0] or b"{}")
        except (OSError, ValueError):
            return {}
    lock.release()
    return None
