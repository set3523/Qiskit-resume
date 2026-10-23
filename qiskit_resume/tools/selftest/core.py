"""Selftest: is this script safe to resume? (D-012) See docs/CONTRACTS.md section 6.2.

Runs the user's script with *synthetic* quantum results (deterministic, derived from
each question's hash - no simulator or QPU needed, any qubit count works):

1. reference run to the end,
2. for several points k: crash hard at call k, then run again,
3. the resumed run must ask exactly the same questions as the reference and finish.

Catches unseeded randomness and incomplete snapshots before any QPU time is spent.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from qiskit_resume.l0_disk.blobs import BlobStore
from qiskit_resume.l0_disk.db import ExperimentDB
from qiskit_resume.l1_record.journal import Journal
from qiskit_resume.l1_record.runs import RunLog
from qiskit_resume.l4_api.run import ENV_FILE, ENV_KILL_AT, ENV_MODE


@dataclass
class SelftestPoint:
    kill_at_call: int
    ok: bool
    first_mismatch: int | None = None
    changed_parts: list[str] = field(default_factory=list)
    hint: str | None = None


@dataclass
class SelftestReport:
    ok: bool
    reference_calls: int
    points: list[SelftestPoint]
    error: str | None = None


def _run(script: Path, args: Sequence[str], env_extra: dict[str, str], timeout_s: float) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop(ENV_KILL_AT, None)
    env.update(env_extra)
    return subprocess.run([sys.executable, str(script), *args], env=env, capture_output=True, text=True,
                          timeout=timeout_s)


def _sequence(path: Path) -> tuple[list[tuple[str, str]], list[dict]]:
    with ExperimentDB.open_read_only(path) as db:
        calls = Journal(db, BlobStore(db)).list_calls()
        events = [e.detail or {} for e in RunLog(db).events(kind="divergence")]
    return [(c.kind, c.key_hash) for c in calls], events


def run_selftest(script: str | os.PathLike, *, args: Sequence[str] = (), points: int = 3,
                 timeout_s: float = 600) -> SelftestReport:
    script = Path(script).resolve()
    with tempfile.TemporaryDirectory(prefix="qresume-selftest-") as tmp:
        tmp_p = Path(tmp)
        ref_file = tmp_p / "reference.qresume"
        ref = _run(script, args, {ENV_MODE: "selftest", ENV_FILE: str(ref_file)}, timeout_s)
        if ref.returncode != 0:
            return SelftestReport(False, 0, [], error="reference run failed:\n" + ref.stderr[-3000:])
        ref_seq, _ = _sequence(ref_file)
        with ExperimentDB.open_read_only(ref_file) as db:
            seed = db.get_meta("seed")
        n = len(ref_seq)
        if n < 2:
            return SelftestReport(True, n, [], error=None)
        ks = sorted({max(1, min(n - 1, round(n * (i + 1) / (points + 1)))) for i in range(points)})
        results = []
        for k in ks:
            f = tmp_p / f"kill_{k}.qresume"
            with ExperimentDB.open_for_run(f) as db:  # same seed as the reference, nothing else
                db.set_meta("seed", seed)
            env = {ENV_MODE: "selftest", ENV_FILE: str(f)}
            crash = _run(script, args, {**env, ENV_KILL_AT: str(k)}, timeout_s)
            if crash.returncode != 77:
                results.append(SelftestPoint(k, False, hint="script did not reach call "
                                             f"#{k} the same way (exit {crash.returncode}): {crash.stderr[-500:]}"))
                continue
            resumed = _run(script, args, env, timeout_s)
            seq, divs = _sequence(f)
            mismatch = next((i for i, (a, b) in enumerate(zip(seq, ref_seq)) if a != b), None)
            if mismatch is None and len(seq) != n:
                mismatch = min(len(seq), n)
            ok = resumed.returncode == 0 and mismatch is None and not divs
            point = SelftestPoint(k, ok, mismatch)
            if divs:
                point.first_mismatch = divs[0].get("position")
                point.changed_parts = divs[0].get("changed_parts", [])
            if not ok:
                if "snapshot" in resumed.stderr:
                    point.hint = "a snapshot is missing some state: add every variable the loop needs to run.snapshot({...})"
                elif point.changed_parts and all(p.endswith(".params") for p in point.changed_parts):
                    point.hint = ("parameter values differ after resuming: likely an unseeded random number "
                                  "(use resume.rng() / resume.seed)")
                else:
                    point.hint = resumed.stderr.strip().splitlines()[-1] if resumed.stderr.strip() else "differs"
            results.append(point)
        return SelftestReport(all(p.ok for p in results), n, results)


def report_text(r: SelftestReport) -> str:
    if r.error:
        return f"selftest could not run: {r.error}"
    lines = [f"reference run: {r.reference_calls} recorded calls"]
    for p in r.points:
        if p.ok:
            lines.append(f"  crash at call #{p.kill_at_call}: resumed identically")
        else:
            where = f" first difference at call #{p.first_mismatch}" if p.first_mismatch is not None else ""
            parts = f" ({', '.join(p.changed_parts)})" if p.changed_parts else ""
            lines.append(f"  crash at call #{p.kill_at_call}: NOT resumable{where}{parts} - {p.hint}")
    lines.append("OK: safe to resume" if r.ok else "FAILED: fix the issues above before spending QPU time")
    return "\n".join(lines)


def report_json(r: SelftestReport) -> str:
    return json.dumps({"ok": r.ok, "reference_calls": r.reference_calls, "error": r.error,
                       "points": [p.__dict__ for p in r.points]}, indent=2)
