"""`qresume` command line (D-012, D-014). See docs/CONTRACTS.md section 6.1.

Exit codes: 0 = ok, 1 = problem found / refused, 2 = usage error.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any, Sequence

from qiskit_resume.l0_disk.blobs import BlobStore
from qiskit_resume.l0_disk.codec import decode
from qiskit_resume.l0_disk.db import (
    ExperimentDB,
    IntegrityError,
    backup_copy,
    running_info,
    verify_file,
)
from qiskit_resume.l1_record.journal import Journal
from qiskit_resume.l1_record.runs import RunLog
from qiskit_resume.l1_record.snapshots import SnapshotStore
from qiskit_resume.l2_replay.budget import Budget
from qiskit_resume.l4_api.run import ENV_FILE, ENV_MODE, ENV_REPORT, prepare_check_copy
from qiskit_resume.tools.selftest import report_json, report_text, run_selftest


def _open_ro(path: str) -> tuple[ExperimentDB, Journal, RunLog, SnapshotStore]:
    db = ExperimentDB.open_read_only(path)
    blobs = BlobStore(db)
    return db, Journal(db, blobs), RunLog(db), SnapshotStore(db, blobs)


def _refuse_if_running(path: str) -> bool:
    info = running_info(path)
    if info is not None:
        print(f"refused: {path} is being run right now ({info}). Try again after it finishes.", file=sys.stderr)
        return True
    return False


# -- status / quota -------------------------------------------------------------------

def cmd_status(a: argparse.Namespace) -> int:
    db, j, runs, snaps = _open_ro(a.file)
    with db:
        calls = j.list_calls(include_invalid=True)
        by_status: dict[str, int] = {}
        by_kind: dict[str, int] = {}
        for c in calls:
            by_status[c.status] = by_status.get(c.status, 0) + 1
            if c.status != "invalid":
                by_kind[c.kind] = by_kind.get(c.kind, 0) + 1
        all_runs = runs.list_runs()
        valid_snaps = snaps.list_snapshots()
        usage = Budget(j, run_id=-1, limit_s=None).summary()
        events = [e for e in runs.events() if e.level in ("warning", "error")][-5:]
        info = {
            "file": a.file,
            "experiment_id": db.get_meta("experiment_id"),
            "seed": db.get_meta("seed"),
            "backend": db.get_meta("backend"),
            "created_at": db.get_meta("created_at"),
            "running": running_info(a.file),
            "runs": len(all_runs),
            "last_run": None if not all_runs else {"status": all_runs[-1].status, "started_at": all_runs[-1].started_at},
            "calls_by_status": by_status,
            "calls_by_kind": by_kind,
            "snapshots": len(valid_snaps),
            "latest_snapshot_position": valid_snaps[-1].position if valid_snaps else None,
            "qpu_used_28d_s": round(usage.used_window_s, 3),
            "recent_problems": [{"at": e.at, "level": e.level, "kind": e.kind, "detail": e.detail} for e in events],
        }
    if a.json:
        print(json.dumps(info, indent=2, default=str))
    else:
        print(f"experiment  {info['file']}  (id {info['experiment_id']}, seed {info['seed']})")
        print(f"device      {info['backend'] or '-'}")
        print(f"running     {'yes ' + str(info['running']) if info['running'] else 'no'}")
        print(f"runs        {info['runs']}" + (f"  (last: {info['last_run']['status']} at {info['last_run']['started_at']})"
                                               if info["last_run"] else ""))
        print(f"calls       {info['calls_by_kind']}  status {info['calls_by_status']}")
        print(f"snapshots   {info['snapshots']}" + (f"  (latest at call #{info['latest_snapshot_position']})"
                                                     if info["snapshots"] else ""))
        print(f"QPU (28d)   {info['qpu_used_28d_s']} s via qiskit-resume")
        for e in info["recent_problems"]:
            print(f"  {e['level']}: {e['kind']} {e['detail']} ({e['at']})")
    return 0


def cmd_quota(a: argparse.Namespace) -> int:
    db, j, _, _ = _open_ro(a.file)
    with db:
        s = Budget(j, run_id=-1, limit_s=None).summary()
    print(json.dumps(s.__dict__, indent=2) if a.json else
          f"QPU used in the last {s.window_days} days (via qiskit-resume): {s.used_window_s:.1f} s\n"
          f"oldest of that usage leaves the window at: {s.next_recovery_at or '-'}\n"
          f"completed jobs with unknown usage: {s.unknown_usage_calls}")
    return 0


# -- copy / check / selftest ------------------------------------------------------------

def cmd_copy(a: argparse.Namespace) -> int:
    dst = backup_copy(a.src, a.dst)
    print(f"consistent copy written: {dst}")
    return 0


def cmd_check(a: argparse.Namespace) -> int:
    try:
        verify_file(a.file)
    except IntegrityError as exc:
        print(f"damaged: {exc}", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory(prefix="qresume-check-") as tmp:
        copy = prepare_check_copy(a.file, tmp)
        report_path = Path(tmp) / "report.json"
        env = dict(os.environ, **{ENV_MODE: "check", ENV_FILE: str(copy), ENV_REPORT: str(report_path)})
        proc = subprocess.run([sys.executable, a.script, *a.args], env=env, capture_output=True, text=True)
        if not report_path.exists():
            print("check could not complete: the script stopped before reaching qiskit-resume\n"
                  + proc.stderr[-3000:], file=sys.stderr)
            return 1
        r = json.loads(report_path.read_text(encoding="utf-8"))
    if a.json:
        print(json.dumps(r, indent=2))
    else:
        print(f"recorded calls: {r['recorded_calls']}, replayed without IBM: {r['replayed']}")
        if r.get("snapshot") is not None:
            print(f"started from snapshot at call #{r['snapshot']}")
        if r["code_changed"]:
            print("script changed since the last run")
        if r["divergence"]:
            d = r["divergence"]
            print(f"DIFFERENT at call #{d['position'] + 1}: {', '.join(d['changed_parts'])}\n  {d['message']}")
        elif r["next_action"] == "none":
            print("the whole script replays from the record; nothing left to run")
        else:
            print(f"OK: continues with real {'reconnection' if r['next_action'] == 'reattach' else 'execution'} "
                  f"at call #{r['next_position'] + 1}")
    return 0 if r["ok"] else 1


def cmd_selftest(a: argparse.Namespace) -> int:
    rep = run_selftest(a.script, args=a.args, points=a.points)
    print(report_json(rep) if a.json else report_text(rep))
    return 0 if rep.ok else 1


# -- export -----------------------------------------------------------------------------

def _result_summary(blobs: BlobStore, ref: str | None, kind: str) -> Any:
    if not ref or kind not in ("estimator", "sampler"):
        return None
    n = decode(blobs.get(ref))
    out = []
    for p in n["pubs"]:
        item: dict[str, Any] = {}
        for name, f in p["data"].items():
            if f["type"] == "array":
                item[name] = f["value"].tolist()
            elif f["type"] == "counts":
                item[name] = f["counts"]
            else:
                from qiskit.primitives.containers import BitArray

                ba = BitArray(f["bits"], int(f["num_bits"]))
                item[name] = ba.get_counts() if ba.shape == () else f"bits{list(ba.shape)}"
        out.append(item)
    return out


def cmd_export(a: argparse.Namespace) -> int:
    db, j, runs, _ = _open_ro(a.file)
    with db:
        blobs = BlobStore(db)
        calib = {}
        for e in runs.events(kind="backend_seen"):
            if e.detail and e.detail.get("calibration"):
                calib[e.run_id] = e.detail["calibration"]
        rows = []
        for c in j.list_calls(include_invalid=a.include_invalid):
            row = {k: getattr(c, k) for k in ("id", "run_id", "seq", "kind", "status", "backend", "job_id",
                                              "usage_s", "duration_s", "created_at", "finished_at", "key_hash")}
            row["calibration"] = calib.get(c.run_id)
            if a.format == "json":
                row["result"] = _result_summary(blobs, c.result_ref, c.kind)
            rows.append(row)
    if a.group_by:
        key = {"backend": "backend", "run": "run_id", "calibration": "calibration"}[a.group_by]
        for r in rows:
            r["group"] = r.get(key)
    if a.format == "json":
        if a.group_by:
            groups: dict[str, list] = {}
            for r in rows:
                groups.setdefault(str(r["group"]), []).append(r)
            text = json.dumps(groups, indent=2, default=str)
        else:
            text = json.dumps(rows, indent=2, default=str)
    else:
        buf = io.StringIO()
        cols = (["group"] if a.group_by else []) + [k for k in rows[0] if k != "group"] if rows else []
        w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        text = buf.getvalue()
    if a.output:
        Path(a.output).write_text(text, encoding="utf-8")
        print(f"written: {a.output}")
    else:
        print(text)
    return 0


# -- pack / unpack ---------------------------------------------------------------------------

def _compressor():
    try:
        from compression import zstd  # Python 3.14+

        return ".zst", zstd.compress, zstd.decompress
    except ImportError:
        import gzip

        return ".gz", gzip.compress, gzip.decompress


def cmd_pack(a: argparse.Namespace) -> int:
    if _refuse_if_running(a.file):
        return 1
    verify_file(a.file)
    ext, comp, _ = _compressor()
    out = Path(a.file + ext)
    if out.exists():
        print(f"refused: {out} exists", file=sys.stderr)
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        tight = Path(tmp) / "tight.qresume"
        conn = sqlite3.connect(a.file)
        try:
            conn.execute("VACUUM INTO ?", (str(tight),))
        finally:
            conn.close()
        out.write_bytes(comp(tight.read_bytes()))
    print(f"packed: {out} ({out.stat().st_size} bytes)")
    return 0


def cmd_unpack(a: argparse.Namespace) -> int:
    src = Path(a.file)
    for ext in (".zst", ".gz"):
        if src.name.endswith(ext):
            dst = Path(str(src)[: -len(ext)])
            break
    else:
        print("unpack expects a .qresume.zst or .qresume.gz file", file=sys.stderr)
        return 2
    if dst.exists():
        print(f"refused: {dst} exists", file=sys.stderr)
        return 1
    if src.name.endswith(".zst"):
        from compression import zstd  # type: ignore[import-not-found]

        data = zstd.decompress(src.read_bytes())
    else:
        import gzip

        data = gzip.decompress(src.read_bytes())
    dst.write_bytes(data)
    verify_file(dst)
    print(f"unpacked: {dst}")
    return 0


# -- fork / compact ----------------------------------------------------------------------------

def cmd_fork(a: argparse.Namespace) -> int:
    with ExperimentDB.open_read_only(a.src) as db:
        snap = SnapshotStore(db, BlobStore(db)).get(a.at_snapshot)
    if not snap.valid:
        print(f"snapshot #{snap.id} is invalid", file=sys.stderr)
        return 1
    backup_copy(a.src, a.to)
    with ExperimentDB.open_for_run(a.to) as db:
        with db.tx() as c:
            c.execute("UPDATE snapshots SET valid=0 WHERE position > ? OR (position = ? AND id != ?)",
                      (snap.position, snap.position, snap.id))
        db.set_meta("experiment_id", uuid.uuid4().hex)
        db.set_meta("forked_from", {"file": str(a.src), "snapshot": snap.id, "position": snap.position})
        db.set_meta("force_next_run", True)
    print(f"forked {a.src} at snapshot #{snap.id} (call #{snap.position}) -> {a.to}\n"
          f"point your script at {a.to}; its first run continues from the snapshot and may differ from there on")
    return 0


def cmd_compact(a: argparse.Namespace) -> int:
    if _refuse_if_running(a.file):
        return 1
    with ExperimentDB.open_for_run(a.file) as db:
        blobs = BlobStore(db)
        j, snaps = Journal(db, blobs), SnapshotStore(db, blobs)
        valid = snaps.list_snapshots()
        keep = {s.id for s in valid[-a.keep_snapshots:]} if a.keep_snapshots > 0 else set()
        with db.tx() as c:
            for s in snaps.list_snapshots(include_invalid=True):
                if (s.valid and s.id not in keep) or (not s.valid and a.drop_invalid):
                    c.execute("DELETE FROM snapshots WHERE id=?", (s.id,))
            if a.drop_invalid:
                c.execute("DELETE FROM calls WHERE status='invalid'")
            if a.before_snapshot is not None:
                pos = snaps.get(a.before_snapshot).position
                c.execute("UPDATE calls SET status='compacted', result_ref=NULL WHERE seq < ? AND status='done'",
                          (pos,))
                c.execute("INSERT INTO meta(key, value) VALUES ('compacted_before', ?) "
                          "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (json.dumps(pos),))
            c.execute("""DELETE FROM blobs WHERE hash NOT IN (
                SELECT result_ref FROM calls WHERE result_ref IS NOT NULL
                UNION SELECT inputs_ref FROM calls WHERE inputs_ref IS NOT NULL
                UNION SELECT state_ref FROM snapshots UNION SELECT rng_ref FROM snapshots WHERE rng_ref IS NOT NULL)""")
        db.conn.execute("VACUUM")
        n = len(j.list_calls(include_invalid=True))
    if a.before_snapshot is not None:
        print("warning: results before the snapshot were removed; this file can now only resume from that snapshot",
              file=sys.stderr)
    print(f"compacted: {a.file} ({n} call records kept, {os.path.getsize(a.file)} bytes)")
    return 0


# -- entry -----------------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qresume", description="inspect and manage qiskit-resume experiment files. "
                                "Script arguments for check/selftest go after --")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("status", help="summary of an experiment file")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("quota", help="QPU usage recorded in the last 28 days")
    s.add_argument("file")
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_quota)

    s = sub.add_parser("copy", help="consistent copy, safe while the experiment is running")
    s.add_argument("src")
    s.add_argument("dst")
    s.set_defaults(fn=cmd_copy)

    s = sub.add_parser("check", help="replay a script against a file without contacting IBM")
    s.add_argument("script")
    s.add_argument("--file", required=True)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("selftest", help="crash/resume a script locally with synthetic results",
                       description="Crash and resume SCRIPT at several points with synthetic results "
                                   "(no QPU time, no account) and compare with an uninterrupted run.",
                       epilog="Script arguments go after --, e.g.  qresume selftest my_vqe.py -- --steps 50")
    s.add_argument("script")
    s.add_argument("--points", type=int, default=3)
    s.add_argument("--json", action="store_true")
    s.set_defaults(fn=cmd_selftest)

    s = sub.add_parser("export", help="export recorded calls")
    s.add_argument("file")
    s.add_argument("--format", choices=("json", "csv"), default="json")
    s.add_argument("--group-by", choices=("backend", "run", "calibration"))
    s.add_argument("--include-invalid", action="store_true")
    s.add_argument("-o", "--output")
    s.set_defaults(fn=cmd_export)

    s = sub.add_parser("pack", help="compress a finished experiment for storage/sharing")
    s.add_argument("file")
    s.set_defaults(fn=cmd_pack)

    s = sub.add_parser("unpack", help="restore a packed experiment")
    s.add_argument("file")
    s.set_defaults(fn=cmd_unpack)

    s = sub.add_parser("fork", help="new experiment file branching from a snapshot")
    s.add_argument("src")
    s.add_argument("--at-snapshot", type=int, required=True)
    s.add_argument("--to", required=True)
    s.set_defaults(fn=cmd_fork)

    s = sub.add_parser("compact", help="tidy a finished experiment (never while running)")
    s.add_argument("file")
    s.add_argument("--keep-snapshots", type=int, default=5)
    s.add_argument("--drop-invalid", action="store_true")
    s.add_argument("--before-snapshot", type=int)
    s.set_defaults(fn=cmd_compact)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    """`qresume CMD [options] [-- script arguments]`"""
    for stream in (sys.stdout, sys.stderr):  # never crash on a console that can't encode a character
        try:
            stream.reconfigure(errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    argv = list(sys.argv[1:] if argv is None else argv)
    script_args: list[str] = []
    if "--" in argv:
        i = argv.index("--")
        argv, script_args = argv[:i], argv[i + 1:]
    a = build_parser().parse_args(argv)
    a.args = script_args
    try:
        return int(a.fn(a))
    except (FileNotFoundError, FileExistsError, KeyError, IntegrityError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
