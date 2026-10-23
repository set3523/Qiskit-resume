"""resume_run(path, **settings) - one experiment run (D-010, D-014). See docs/CONTRACTS.md section 5.1.

Assembles the lower layers. Wrapped objects (primitive / pass manager / function)
find the active run through :func:`get_active_run` and ask it to decide each call.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import platform
import secrets
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from qiskit_resume._version import __version__
from qiskit_resume.l0_disk.blobs import BlobStore
from qiskit_resume.l0_disk.db import ExperimentDB, backup_copy
from qiskit_resume.l1_record.journal import Journal
from qiskit_resume.l1_record.runs import RunLog
from qiskit_resume.l1_record.snapshots import SnapshotStore
from qiskit_resume.l2_replay.budget import Budget
from qiskit_resume.l2_replay.divergence import apply as apply_verdict
from qiskit_resume.l2_replay.divergence import decide
from qiskit_resume.l2_replay.replay import Decision, ReplayEngine
from qiskit_resume.l4_api import rng as _rng
from qiskit_resume.l4_api import snapshot as _snapshot
from qiskit_resume.l4_api.rng import NoActiveRunError

# env hooks used by the CLI (check / selftest); not part of the user API
ENV_MODE = "QRESUME_MODE"            # "check" | "selftest"
ENV_FILE = "QRESUME_FILE"            # experiment file to use instead of the script's path
ENV_REPORT = "QRESUME_REPORT"        # where check/selftest write their JSON report
ENV_KILL_AT = "QRESUME_KILL_AT"      # selftest: simulate a crash at this call position


class DivergenceStop(RuntimeError):
    """Replay found a different question and the rules say stop (message explains why and how)."""


class BackendChangedStop(RuntimeError):
    """The device changed and on_backend_change='stop'."""


_ACTIVE: list["ResumeRun"] = []


def get_active_run() -> "ResumeRun":
    if not _ACTIVE:
        raise NoActiveRunError()
    return _ACTIVE[-1]


def resume_run(
    path: str | os.PathLike,
    *,
    seed: int | None = None,
    force: bool = False,
    on_divergence: str = "warn",
    on_backend_change: str = "warn",
    qpu_budget_s: float | None = None,
    job_wait_timeout_s: float | None = None,
    job_max_retries: int = 5,
    sampler_storage: str = "raw",
    use_snapshots: bool = True,
    slow_report_s: float = 10.0,
    quiet: bool = False,
    log_calls: bool = False,
) -> "ResumeRun":
    if on_divergence not in ("warn", "stop"):
        raise ValueError("on_divergence must be 'warn' or 'stop'")
    if on_backend_change not in ("warn", "stop"):
        raise ValueError("on_backend_change must be 'warn' or 'stop'")
    if sampler_storage not in ("raw", "counts"):
        raise ValueError("sampler_storage must be 'raw' or 'counts'")
    settings = {
        "force": force, "on_divergence": on_divergence, "on_backend_change": on_backend_change,
        "qpu_budget_s": qpu_budget_s, "job_wait_timeout_s": job_wait_timeout_s,
        "job_max_retries": int(job_max_retries), "sampler_storage": sampler_storage,
        "use_snapshots": use_snapshots, "slow_report_s": slow_report_s, "quiet": quiet,
    }
    r = ResumeRun(Path(path), seed=seed, settings=settings)
    r.log_calls = bool(log_calls)  # console log only; not stored in the record
    return r


def _code_fingerprint() -> str | None:
    script = Path(sys.argv[0]) if sys.argv and sys.argv[0] else None
    main = sys.modules.get("__main__")
    main_file = getattr(main, "__file__", None)
    for cand in (Path(main_file) if main_file else None, script):
        if cand is not None and cand.is_file() and cand.suffix == ".py":
            h = hashlib.sha256(cand.read_bytes())
            h.update(json.dumps(sys.argv[1:]).encode())  # script arguments change behaviour too
            return h.hexdigest()
    return None


def _env() -> dict[str, Any]:
    env: dict[str, Any] = {"python": platform.python_version(), "platform": platform.platform(),
                           "qiskit_resume": __version__}
    from importlib import metadata

    for dist in ("qiskit", "qiskit-ibm-runtime", "numpy", "scipy", "qiskit-algorithms"):
        try:
            env[dist] = metadata.version(dist)  # reads package metadata; does not import (fast)
        except metadata.PackageNotFoundError:
            pass
    return env


def _safe_print(text: str, stream: Any) -> None:
    """Print without crashing on consoles that cannot encode some characters (e.g. Windows cp949)."""
    try:
        print(text, file=stream, flush=True)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"), file=stream, flush=True)


def _caller_location() -> str | None:
    for frame in inspect.stack()[2:]:
        fn = frame.filename.replace("\\", "/")
        if "/qiskit_resume/" not in fn and "/qiskit/" not in fn and not fn.startswith("<"):
            return f"{os.path.basename(fn)}:{frame.lineno}"
    return None


class ResumeRun:
    def __init__(self, path: Path, *, seed: int | None, settings: dict[str, Any]) -> None:
        self.requested_path = path
        self._seed_arg = seed
        self.settings = settings
        self.mode = os.environ.get(ENV_MODE) or None
        self.path = Path(os.environ[ENV_FILE]) if os.environ.get(ENV_FILE) else path
        self.run_id: int = -1
        self.is_new = False
        self.seed: int = -1
        self.code_changed = False
        self.force = bool(settings["force"])
        self._loaded_snapshot = None
        self._snapshot_guard_until = -1
        self.stats = {"replay": 0, "reattach": 0, "execute": 0}
        self._first_divergence: dict[str, Any] | None = None
        self._backends_seen: set[str] = set()
        self._gaps: list[tuple[float, int, str | None]] = []
        self._last_mark = time.perf_counter()
        self.log_calls = False
        self._replay_streak = 0

    # -- lifecycle ------------------------------------------------------------------

    def __enter__(self) -> "ResumeRun":
        self.db = ExperimentDB.open_for_run(self.path)
        try:
            self.blobs = BlobStore(self.db)
            self.journal = Journal(self.db, self.blobs)
            self.runs = RunLog(self.db)
            self.snaps = SnapshotStore(self.db, self.blobs)
            prev_runs = self.runs.list_runs()
            self.is_new = not prev_runs and self.journal.max_seq() is None

            if self.is_new:
                self.runs.fix_immutable("experiment_id", uuid.uuid4().hex)
            seed_value = self._seed_arg
            if seed_value is None and self.db.get_meta("seed") is None:
                seed_value = secrets.randbits(31)  # new experiment: random seed, recorded for every replay
            self.seed = int(self.runs.fix_immutable("seed", seed_value))

            if self.db.get_meta("force_next_run"):
                self.force = True
                self.db.set_meta("force_next_run", None)

            fp = _code_fingerprint()
            prev = prev_runs[-1] if prev_runs else None
            self.code_changed = bool(prev and prev.code_fingerprint and fp and prev.code_fingerprint != fp)
            rec = self.runs.start(code_fingerprint=fp, settings=self.settings, env=_env())
            self.run_id = rec.id

            if self.is_new:
                self.runs.log_event(self.run_id, "info", "new_experiment", {"path": str(self.path)})
                self._say("info", f"new experiment: {self.path} (no previous record)")
            else:
                if prev and prev.settings != self.settings:
                    changed = {k: [prev.settings.get(k), v] for k, v in self.settings.items()
                               if prev.settings.get(k) != v}
                    self.runs.log_event(self.run_id, "info", "settings_changed", changed)
                    self._say("info", "settings changed: " + ", ".join(f"{k} {a!r} -> {b!r}"
                                                                     for k, (a, b) in changed.items()))
                if self.code_changed:
                    self.runs.log_event(self.run_id, "info", "code_changed", None)
                n = len(self.journal.list_calls())
                self._say("info", f"resuming {self.path}: {n} recorded calls will be replayed first")

            self.engine = ReplayEngine(self.journal, run_id=self.run_id)
            self.budget = Budget(self.journal, run_id=self.run_id, limit_s=self.settings["qpu_budget_s"])
            _rng.activate(self.seed)
            _rng.apply_global_seed(self.seed)
        except BaseException:
            self.db.close()
            raise
        _ACTIVE.append(self)
        self._last_mark = time.perf_counter()
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        try:
            if self in _ACTIVE:
                _ACTIVE.remove(self)
            _rng.deactivate()
            status = "finished" if exc_type is None else "aborted"
            if self.mode == "check" and exc_type is None:
                self._write_check_report({"ok": True, "replayed": self.stats["replay"], "next_position": None,
                                          "next_action": "none", "divergence": None,
                                          "code_changed": self.code_changed, "snapshot": None,
                                          "recorded_calls": len(self.journal.list_calls())})
            self.runs.finish(self.run_id, status)
            if exc_type is None:
                self._report_slow()
            s = self.stats
            used = self.budget.summary().used_run_s
            self._say("info", f"run {status}: replayed {s['replay']}, reattached {s['reattach']}, "
                              f"executed {s['execute']} calls; QPU used this run {used:.1f}s")
        finally:
            self.db.close()

    # -- user API -------------------------------------------------------------------

    def snapshot(self, state: dict[str, Any]) -> int:
        return _snapshot.save(self, state)

    def load_snapshot(self) -> dict[str, Any] | None:
        return _snapshot.load(self)

    def usage(self):
        return self.budget.summary()

    @property
    def position(self) -> int:
        return self.engine.position

    # -- used by wrapped objects ------------------------------------------------------

    def _decide(self, kind: str, key: Any) -> Decision:
        now = time.perf_counter()
        gap = now - self._last_mark
        d = self.engine.next(kind, key)
        if gap >= 1.0:
            self._gaps.append((gap, d.position, _caller_location()))

        if self.mode == "selftest" and os.environ.get(ENV_KILL_AT) and d.position == int(os.environ[ENV_KILL_AT]):
            os._exit(77)  # simulate a crash: no cleanup at all

        # a divergence anywhere in the replayed stretch after a snapshot means the snapshot missed some state
        from_snapshot = self._loaded_snapshot is not None and d.position <= self._snapshot_guard_until
        if d.divergence is not None:
            if self._first_divergence is None:
                self._first_divergence = {"position": d.position, "changed_parts": d.divergence.changed_parts}
            v = decide(d.divergence, code_changed=self.code_changed, started_from_snapshot=from_snapshot,
                       force=self.force, on_divergence=self.settings["on_divergence"])
            if from_snapshot and v.action == "stop":
                self.snaps.invalidate(self._loaded_snapshot.id)
                self.runs.log_event(self.run_id, "error", "snapshot_invalid", {"snapshot_id": self._loaded_snapshot.id})
            if self.mode == "check":
                self._finish_check(d, verdict=v.message)
            apply_verdict(v, d.divergence, journal=self.journal, runs=self.runs, run_id=self.run_id)
            self._say(v.level, v.message)
            if v.action == "stop":
                raise DivergenceStop(v.message)
        if self._loaded_snapshot is not None and d.position >= self._snapshot_guard_until:
            self._loaded_snapshot = None  # caught up with the record: the snapshot is verified
        if self.mode == "check" and d.action != "replay":
            self._finish_check(d)
        self.stats[d.action] += 1
        if self.log_calls:
            self._log_call(kind, d)
        return d

    def _log_call(self, kind: str, d: Decision) -> None:
        """One console line per call; replayed stretches are summarized (every 100th line)."""
        n = d.position + 1
        if d.action == "replay":
            self._replay_streak += 1
            if self._replay_streak == 1 or n % 100 == 0:
                _safe_print(f"[qiskit-resume] call #{n} {kind}: replay (result taken from the record)", sys.stderr)
            return
        if self._replay_streak:
            _safe_print(f"[qiskit-resume] replayed {self._replay_streak} recorded calls; "
                        f"from call #{n} on, calls run for real", sys.stderr)
            self._replay_streak = 0
        _safe_print(f"[qiskit-resume] call #{n} {kind}: {d.action}", sys.stderr)

    def _mark_done(self) -> None:
        self._last_mark = time.perf_counter()

    def _check_backend(self, name: str | None, backend_obj: Any = None) -> None:
        if not name or name in self._backends_seen:
            return
        self._backends_seen.add(name)
        first = self.db.get_meta("backend")
        if first is None:
            self.db.set_meta("backend", name)
        detail: dict[str, Any] = {"backend": name}
        if backend_obj is not None:
            from qiskit_resume.l3_qiskit.ibm import calibration_time

            cal = calibration_time(backend_obj)
            if cal:
                detail["calibration"] = cal
        self.runs.log_event(self.run_id, "info", "backend_seen", detail)
        if first is not None and first != name:
            msg = (f"device changed: this experiment started on {first!r}, now running on {name!r}. "
                   "Earlier results come from the old device.")
            self.runs.log_event(self.run_id, "warning", "backend_changed", {"from": first, "to": name})
            if self.settings["on_backend_change"] == "stop":
                raise BackendChangedStop(msg + " Stopping because on_backend_change='stop'.")
            self._say("warning", msg)

    def _say(self, level: str, msg: str) -> None:
        if self.settings.get("quiet") and level == "info":
            return
        _safe_print(f"[qiskit-resume] {level}: {msg}", sys.stderr)

    # -- reports ---------------------------------------------------------------------

    def _report_slow(self) -> None:
        limit = self.settings.get("slow_report_s") or 0
        slow = sorted((g for g in self._gaps if g[0] >= limit), reverse=True)[:3]
        if not limit or not slow:
            return
        lines = [f"{g:.0f}s before call #{pos}" + (f" (around {loc})" if loc else "") for g, pos, loc in slow]
        self._say("info", "slow classical sections (they run again on every replay): " + "; ".join(lines)
                  + ". If such a section has no side effects, put it in a function and wrap it with @resume.wrap.")

    def _finish_check(self, d: Decision, verdict: str | None = None) -> None:
        report = {
            "ok": d.divergence is None,
            "replayed": self.stats["replay"],
            "next_position": d.position,
            "next_action": d.action,
            "divergence": None if d.divergence is None else {
                "position": d.position, "changed_parts": d.divergence.changed_parts, "message": verdict},
            "code_changed": self.code_changed,
            "snapshot": None if self._loaded_snapshot is None else self._loaded_snapshot.position,
            "recorded_calls": len(self.journal.list_calls()),
        }
        self._write_check_report(report)
        os._exit(0)

    @staticmethod
    def _write_check_report(report: dict[str, Any]) -> None:
        out = os.environ.get(ENV_REPORT)
        if out:
            Path(out).write_text(json.dumps(report), encoding="utf-8")


def prepare_check_copy(src: str | os.PathLike, workdir: str | os.PathLike) -> Path:
    """For `qresume check`: a consistent private copy so checking never touches the real file."""
    dst = Path(workdir) / "check.qresume"
    if dst.exists():
        dst.unlink()
    return backup_copy(src, dst)
