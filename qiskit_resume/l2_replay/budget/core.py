"""QPU usage and budget (D-009). See docs/CONTRACTS.md section 3.3."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from qiskit_resume.l1_record.journal import Journal


@dataclass(frozen=True)
class UsageSummary:
    window_days: int
    used_window_s: float
    used_run_s: float
    limit_s: float | None
    remaining_s: float | None
    next_recovery_at: str | None
    unknown_usage_calls: int


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))


def _fmt(t: dt.datetime) -> str:
    return t.astimezone(dt.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Budget:
    def __init__(self, journal: Journal, *, run_id: int, limit_s: float | None, window_days: int = 28) -> None:
        self._j, self.run_id, self.limit_s, self.window_days = journal, run_id, limit_s, window_days

    def summary(self, *, now: str | None = None) -> UsageSummary:
        t_now = _parse(now) if now else dt.datetime.now(dt.timezone.utc)
        start = t_now - dt.timedelta(days=self.window_days)
        used_w = used_r = 0.0
        unknown = 0
        oldest: dt.datetime | None = None
        for r in self._j.list_calls(include_invalid=True):
            if r.kind not in ("estimator", "sampler") or r.finished_at is None or r.status not in ("done", "invalid"):
                continue
            if r.usage_s is None:
                if r.status == "done" and r.job_id:
                    unknown += 1
                continue
            t = _parse(r.finished_at)
            if t >= start:
                used_w += r.usage_s
                oldest = t if oldest is None or t < oldest else oldest
            if r.run_id == self.run_id:
                used_r += r.usage_s
        remaining = None if self.limit_s is None else max(0.0, self.limit_s - used_r)
        nxt = _fmt(oldest + dt.timedelta(days=self.window_days)) if oldest else None
        return UsageSummary(self.window_days, used_w, used_r, self.limit_s, remaining, nxt, unknown)

    def estimate(self, kind: str, key) -> float | None:
        circuit_parts = {k: v for k, v in key.parts.items() if k.endswith(".circuit")}
        samples = [r.usage_s for r in self._j.list_calls(include_invalid=True)
                   if r.kind == kind and r.usage_s and {k: r.parts.get(k) for k in circuit_parts} == circuit_parts]
        return None if not samples else 1.2 * sum(samples) / len(samples)

    def can_submit(self, estimate_s: float | None) -> bool:
        if self.limit_s is None:
            return True
        remaining = self.summary().remaining_s or 0.0
        return remaining > 0 if estimate_s is None else remaining >= estimate_s
