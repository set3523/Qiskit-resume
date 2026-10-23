import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from qiskit_resume.l0_disk.blobs import BlobStore
from qiskit_resume.l0_disk.db import (
    AlreadyRunningError,
    ExperimentDB,
    IntegrityError,
    backup_copy,
    verify_file,
)

ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------- db basics

def test_create_and_reopen(tmp_path):
    p = tmp_path / "exp.qresume"
    with ExperimentDB.open_for_run(p) as db:
        assert db.get_meta("format_version") == 1
        blobs = BlobStore(db)
        with db.tx():
            h = blobs.put(b"hello")
        with db.tx():
            h2 = blobs.put(b"hello")  # dedup
        assert h == h2
    with ExperimentDB.open_read_only(p) as db:
        assert BlobStore(db).get(h) == b"hello"
        assert db.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 1


def test_transaction_rolls_back_on_error(tmp_path):
    p = tmp_path / "exp.qresume"
    with ExperimentDB.open_for_run(p) as db:
        blobs = BlobStore(db)
        with pytest.raises(ZeroDivisionError):
            with db.tx():
                blobs.put(b"partial")
                1 / 0
        assert db.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] == 0


# ---------------------------------------------------------------- single writer

def _spawn(code: str) -> subprocess.Popen:
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    return subprocess.Popen([sys.executable, "-c", textwrap.dedent(code)], env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


WRITER = """
import sys, time
from qiskit_resume.l0_disk.db import ExperimentDB
from qiskit_resume.l0_disk.blobs import BlobStore
db = ExperimentDB.open_for_run({path!r})
blobs = BlobStore(db)
i = 0
while True:
    with db.tx():
        blobs.put(("record-%d" % i).encode() * 200)
    i += 1
    if i == 5:
        print("ready", flush=True)
"""


def test_second_writer_is_refused_and_readers_allowed(tmp_path):
    p = tmp_path / "exp.qresume"
    proc = _spawn(WRITER.format(path=str(p)))
    try:
        assert proc.stdout.readline().strip() == "ready"
        with pytest.raises(AlreadyRunningError) as e:
            ExperimentDB.open_for_run(p)
        assert "pid" in str(e.value)
        with ExperimentDB.open_read_only(p) as ro:  # reading while the writer runs
            assert ro.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] >= 5
    finally:
        proc.kill()
        proc.wait()


def test_kill_mid_write_leaves_valid_file_and_releases_lock(tmp_path):
    p = tmp_path / "exp.qresume"
    proc = _spawn(WRITER.format(path=str(p)))
    assert proc.stdout.readline().strip() == "ready"
    time.sleep(0.3)  # keep writing
    proc.kill()  # SIGKILL on POSIX, TerminateProcess on Windows: no cleanup at all
    proc.wait()
    verify_file(p)  # not damaged
    with ExperimentDB.open_for_run(p) as db:  # OS released the lock
        n = db.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0]
        assert n >= 5
        for (data,) in db.conn.execute("SELECT data FROM blobs"):
            assert bytes(data).startswith(b"record-")  # every stored record is complete


def test_backup_copy_while_writing(tmp_path):
    p = tmp_path / "exp.qresume"
    proc = _spawn(WRITER.format(path=str(p)))
    try:
        assert proc.stdout.readline().strip() == "ready"
        dst = backup_copy(p, tmp_path / "copy.qresume")
        verify_file(dst)
        with ExperimentDB.open_read_only(dst) as db:
            assert db.conn.execute("SELECT COUNT(*) FROM blobs").fetchone()[0] >= 5
    finally:
        proc.kill()
        proc.wait()


def test_damaged_copy_is_detected(tmp_path):
    p = tmp_path / "exp.qresume"
    with ExperimentDB.open_for_run(p) as db:
        for i in range(200):
            with db.tx():
                BlobStore(db).put(os.urandom(2000))
    bad = tmp_path / "bad.qresume"
    data = p.read_bytes()
    bad.write_bytes(data[: len(data) // 2])  # simulate a torn raw copy
    with pytest.raises(IntegrityError):
        verify_file(bad)


def test_same_journal_mode_everywhere_and_read_only_open_without_uri(tmp_path):
    p = tmp_path / "e.qresume"
    with ExperimentDB.open_for_run(p) as db:
        assert db.journal_mode == "delete"  # works the same on local and network drives
    with ExperimentDB.open_read_only(p) as ro:  # plain path (URIs reject \\server\share paths)
        with pytest.raises(Exception):
            ro.conn.execute("CREATE TABLE x(a)")  # query_only
