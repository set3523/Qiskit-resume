"""Wrapped Estimator / Sampler (D-005, D-008, D-009). See docs/CONTRACTS.md section 5.4."""

from __future__ import annotations

import time
from typing import Any, Sequence

import numpy as np

from qiskit_resume.l0_disk.codec import decode, encode
from qiskit_resume.l1_record.hashing import digest_parts
from qiskit_resume.l3_qiskit import ibm
from qiskit_resume.l3_qiskit.circuits import backend_name, pub_shapes, question_parts
from qiskit_resume.l3_qiskit.results import from_neutral, to_neutral
from qiskit_resume.l4_api.run import get_active_run


class BudgetExceeded(RuntimeError):
    """qpu_budget_s reached: stopped cleanly before submitting the next job. Run again later to continue."""


class QuotaExhausted(RuntimeError):
    """IBM reported that the QPU quota is used up. Run again after it recovers."""


class JobWaitTimeout(TimeoutError):
    """Waited job_wait_timeout_s for a queued job. The job is kept; the next run reconnects to it."""


def _options(inner: Any) -> dict[str, Any] | None:
    opts: dict[str, Any] = {}
    for attr in ("default_precision", "default_shots"):
        if hasattr(inner, attr):
            opts[attr] = getattr(inner, attr)
    o = getattr(inner, "options", None)
    if o is not None:
        try:
            import dataclasses

            opts["options"] = dataclasses.asdict(o) if dataclasses.is_dataclass(o) else str(o)
        except Exception:  # noqa: BLE001
            pass
    return opts or None


class WrappedPrimitive:
    _qresume_wrapped = True

    def __init__(self, inner: Any, kind: str) -> None:
        self._inner = inner
        self._kind = kind

    def run(self, pubs: Sequence[Any], **kwargs: Any) -> "WrappedJob":
        run = get_active_run()
        with run.db.lock:  # libraries may call from worker threads: decide + submit as one step
            return self._run_locked(run, pubs, kwargs)

    def _run_locked(self, run: Any, pubs: Sequence[Any], kwargs: dict[str, Any]) -> "WrappedJob":
        precision = kwargs.get("precision") if self._kind == "estimator" else None
        shots = kwargs.get("shots") if self._kind == "sampler" else None
        parts = question_parts(self._kind, pubs, precision=precision, shots=shots, options=_options(self._inner))
        key = digest_parts(parts)
        backend = backend_name(self._inner)
        run._check_backend(backend)  # before deciding: a device-change stop is not a call
        decision = run._decide(self._kind, key)
        job = WrappedJob(self, run, decision, key, pubs, kwargs, backend)
        if decision.action == "execute":
            job._submit()
        return job

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def __repr__(self) -> str:
        return f"resume.wrap({self._inner!r})"


# Subclass the Qiskit V2 base classes so library code that checks
# isinstance(x, BaseEstimatorV2 / BaseSamplerV2) (e.g. qiskit_algorithms ComputeUncompute) accepts us.
from qiskit.primitives import BaseEstimatorV2 as _BaseEstimatorV2  # noqa: E402
from qiskit.primitives import BaseSamplerV2 as _BaseSamplerV2  # noqa: E402


class WrappedEstimator(WrappedPrimitive, _BaseEstimatorV2):
    def __init__(self, inner: Any) -> None:
        WrappedPrimitive.__init__(self, inner, "estimator")

    def run(self, pubs, *, precision=None):  # type: ignore[override]
        kwargs = {} if precision is None else {"precision": precision}
        return WrappedPrimitive.run(self, pubs, **kwargs)


class WrappedSampler(WrappedPrimitive, _BaseSamplerV2):
    def __init__(self, inner: Any) -> None:
        WrappedPrimitive.__init__(self, inner, "sampler")

    def run(self, pubs, *, shots=None):  # type: ignore[override]
        kwargs = {} if shots is None else {"shots": shots}
        return WrappedPrimitive.run(self, pubs, **kwargs)


class WrappedJob:
    def __init__(self, prim: WrappedPrimitive, run: Any, decision: Any, key: Any, pubs: Any,
                 kwargs: dict[str, Any], backend: str | None) -> None:
        self._p, self._run, self._d, self._key = prim, run, decision, key
        self._pubs, self._kwargs, self._backend = pubs, kwargs, backend
        self._rec = decision.record
        self._job: Any = None
        self._result: Any = None
        self._t0 = time.perf_counter()

    # -- submission ------------------------------------------------------------------

    def _submit(self, *, retry_of: Any = None) -> None:
        run = self._run
        if run.mode != "selftest":
            est = run.budget.estimate(self._p._kind, self._key)
            if not run.budget.can_submit(est):
                run.runs.log_event(run.run_id, "warning", "budget_stop", {"call": self._d.position + 1})
                run.stats["execute"] -= 1  # decided but never submitted: not an executed call
                raise BudgetExceeded(
                    f"qpu_budget_s={run.settings['qpu_budget_s']} reached before call #{self._d.position + 1}; "
                    "stopped cleanly. Run again (with a new budget) to continue from here.")
        if self._rec is not None and self._rec.status != "invalid":
            run.journal.invalidate([self._rec.id])  # failed/unsubmitted attempt: keep it as history
        if run.mode == "selftest":
            self._job = None
            job_id = None
        else:
            self._job = self._p._inner.run(self._pubs, **self._kwargs)
            job_id = self._job.job_id() if ibm.is_runtime_job(self._job) else None
        self._rec = run.journal.record_submitted(
            run_id=run.run_id, seq=self._d.position, kind=self._p._kind, key=self._key,
            occurrence=self._d.occurrence, job_id=job_id, backend=self._backend)  # before waiting (D-008)
        if job_id and getattr(run, "log_calls", False):
            run._say("info", f"call #{self._d.position + 1} submitted as job {job_id}")
        self._t0 = time.perf_counter()

    # -- results ---------------------------------------------------------------------

    def result(self, timeout: float | None = None) -> Any:
        if self._result is not None:
            return self._result
        run, action = self._run, self._d.action
        if action == "replay":
            with run.db.lock:
                self._result = from_neutral(decode(run.blobs.get(self._rec.result_ref)))
            run._mark_done()
            return self._result
        if action == "replay":
            self._result = from_neutral(decode(run.blobs.get(self._rec.result_ref)))
        elif action == "reattach":
            self._result = self._reattach(timeout)
        else:
            self._result = self._wait(timeout)
        run._mark_done()
        return self._result

    def _reattach(self, timeout: float | None) -> Any:
        run, rec = self._run, self._rec
        job = ibm.fetch_job(ibm.service_of(self._p._inner), rec.job_id)
        if job is None:
            run.runs.log_event(run.run_id, "info", "job_resubmitted", {"job_id": rec.job_id, "reason": "not found"})
            run._say("info", f"job {rec.job_id} could not be found; submitting call #{self._d.position + 1} again")
            self._submit()
            return self._wait(timeout)
        state = ibm.job_state(job)
        if state in ("failed", "cancelled"):
            kind = ibm.classify_error(job) if state == "failed" else "cancelled"
            if kind == "quota":
                self._fail_quota(job, "job failed: quota")
            run.journal.record_failed(rec.id, error=f"{state}: {kind}")
            run.runs.log_event(run.run_id, "info", "job_resubmitted", {"job_id": rec.job_id, "reason": kind})
            run._say("info", f"job {rec.job_id} was {state}; submitting call #{self._d.position + 1} again")
            self._rec = run.journal.get(rec.id)
            self._submit()
            return self._wait(timeout)
        run.runs.log_event(run.run_id, "info", "job_reattached", {"job_id": rec.job_id, "state": state})
        run._say("info", f"reconnected to job {rec.job_id} ({state}) for call #{self._d.position + 1} - no new QPU time")
        self._job = job
        return self._wait(timeout)

    def _wait(self, timeout: float | None) -> Any:
        run = self._run
        if run.mode == "selftest":
            return self._store(_synthetic_result(self._p._kind, self._pubs, self._kwargs, self._key.key_hash), None)
        retries = 0
        wait = timeout if timeout is not None else run.settings.get("job_wait_timeout_s")
        while True:
            job = self._job
            runtime = ibm.is_runtime_job(job)
            try:
                res = job.result(timeout=wait) if (runtime and wait is not None) else job.result()
            except Exception as exc:  # noqa: BLE001
                if runtime and "timeout" in type(exc).__name__.lower():
                    raise JobWaitTimeout(
                        f"job {self._rec.job_id} still waiting after {wait}s; it is kept and the next run "
                        "reconnects to it.") from exc
                kind = ibm.classify_error(job, str(exc)) if runtime else "unknown"
                if kind == "quota":
                    self._fail_quota(job, str(exc))
                if runtime and kind in ("device", "ttl") and retries < run.settings["job_max_retries"]:
                    retries += 1
                    run.runs.log_event(run.run_id, "warning", "job_retry",
                                       {"job_id": self._rec.job_id, "attempt": retries, "error": str(exc)[:500]})
                    run._say("warning", f"call #{self._d.position + 1}: {kind} error, retry {retries}/"
                                        f"{run.settings['job_max_retries']}")
                    self._job = self._p._inner.run(self._pubs, **self._kwargs)
                    self._rec = run.journal.set_job(self._rec.id, self._job.job_id())
                    continue
                run.journal.record_failed(self._rec.id, error=f"{kind}: {exc}"[:2000])
                raise
            return self._store(res, ibm.job_usage_s(job) if runtime else None)

    def _store(self, res: Any, usage: float | None) -> Any:
        with self._run.db.lock:
            return self._store_locked(res, usage)

    def _store_locked(self, res: Any, usage: float | None) -> Any:
        run = self._run
        data = encode(to_neutral(res, kind=self._p._kind, sampler_storage=run.settings["sampler_storage"]))
        with run.db.tx():
            ref = run.blobs.put(data, kind="result")
        self._rec = run.journal.record_done(self._rec.id, result_ref=ref, usage_s=usage,
                                            duration_s=time.perf_counter() - self._t0)
        if usage is not None and getattr(run, "log_calls", False):
            u = run.budget.summary()
            limit = f" of budget {u.limit_s:g}s" if u.limit_s is not None else ""
            run._say("info", f"call #{self._d.position + 1} done: QPU {usage:.1f}s; this run {u.used_run_s:.1f}s{limit}; "
                             f"last 28 days {u.used_window_s:.1f}s (this file)")
        return res

    def _fail_quota(self, job: Any, msg: str) -> None:
        run = self._run
        run.journal.record_failed(self._rec.id, error=f"quota: {msg}"[:2000])
        run.runs.log_event(run.run_id, "error", "quota_exhausted", {"call": self._d.position + 1})
        s = run.budget.summary()
        when = f" (estimated recovery from {s.next_recovery_at})" if s.next_recovery_at else ""
        raise QuotaExhausted(f"IBM QPU quota exhausted at call #{self._d.position + 1}. Run again after it "
                             f"recovers{when}; everything before this call is kept.")

    # -- passthrough -------------------------------------------------------------------

    def job_id(self) -> str | None:
        if self._rec is not None and self._rec.job_id:
            return self._rec.job_id
        return self._job.job_id() if self._job is not None and hasattr(self._job, "job_id") else None

    def status(self) -> str:
        if self._result is not None or self._d.action == "replay":
            return "DONE"
        if self._job is not None:
            s = self._job.status()
            return getattr(s, "name", str(s))
        return "QUEUED"

    def done(self) -> bool:
        return self.status() == "DONE"

    def __getattr__(self, name: str) -> Any:
        if self._job is None:
            raise AttributeError(name)
        return getattr(self._job, name)


def _synthetic_result(kind: str, pubs: Any, kwargs: dict[str, Any], key_hash: str) -> Any:
    """selftest: deterministic fake results with the right shapes, derived from the question hash."""
    from qiskit.primitives.containers import BitArray, DataBin, PrimitiveResult, PubResult, SamplerPubResult

    g = np.random.default_rng(int(key_hash[:16], 16))
    out = []
    for info in pub_shapes(kind, pubs, precision=kwargs.get("precision"), shots=kwargs.get("shots")):
        shape = info["shape"]
        if kind == "estimator":
            out.append(PubResult(DataBin(shape=shape, evs=g.uniform(-1, 1, shape), stds=np.zeros(shape)),
                                 metadata={"selftest": True}))
        else:
            fields = {}
            for name, size in info["cregs"]:
                nbytes = (size + 7) // 8
                arr = g.integers(0, 256, size=shape + (info["shots"], nbytes), dtype=np.uint8)
                if size % 8:
                    arr[..., 0] &= (1 << (size % 8)) - 1
                fields[name] = BitArray(arr, size)
            out.append(SamplerPubResult(DataBin(shape=shape, **fields), metadata={"selftest": True}))
    return PrimitiveResult(out, metadata={"selftest": True})

