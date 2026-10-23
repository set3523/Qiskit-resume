"""IBM Runtime job helpers (D-008, D-009, D-015). See docs/CONTRACTS.md section 4.3.

Duck-typed so it works without qiskit-ibm-runtime installed (local primitives)
and is testable with fake jobs. Only this module knows IBM job details.
"""

from __future__ import annotations

from typing import Any

_STATE = {
    "INITIALIZING": "queued", "QUEUED": "queued", "VALIDATING": "queued",
    "RUNNING": "running", "DONE": "done", "ERROR": "failed", "CANCELLED": "cancelled",
}

_ERROR_WORDS = [
    ("quota", ("quota", "usage limit", "insufficient", "exceeded your", "no remaining", "out of time")),
    ("ttl", ("ttl", "time to live", "expired", "max_time", "interactive timeout")),
    ("circuit", ("isa", "not supported", "invalid", "transpil", "instruction", "too many qubits", "unbound")),
    ("device", ("backend", "maintenance", "internal", "calibration", "unavailable", "offline", "retry")),
]


def available() -> bool:
    try:
        import qiskit_ibm_runtime  # noqa: F401
    except ImportError:
        return False
    return True


def is_runtime_job(job: Any) -> bool:
    mod = type(job).__module__
    return mod.startswith("qiskit_ibm_runtime") or bool(getattr(job, "_qresume_runtime_like", False))


def job_state(job: Any) -> str:
    try:
        s = job.status()
    except Exception:  # noqa: BLE001
        return "unknown"
    name = getattr(s, "name", s)
    return _STATE.get(str(name).upper(), "unknown")


def classify_error(job: Any, message: str | None = None) -> str:
    msg = message
    if msg is None:
        try:
            msg = job.error_message() if job is not None else ""
        except Exception:  # noqa: BLE001
            msg = ""
    low = (msg or "").lower()
    for kind, words in _ERROR_WORDS:
        if any(w in low for w in words):
            return kind
    return "unknown"


def job_usage_s(job: Any) -> float | None:
    try:
        u = job.usage()
        if u:
            return float(u)
    except Exception:  # noqa: BLE001
        pass
    try:
        q = job.metrics().get("usage", {}).get("quantum_seconds")
        if q:
            return float(q)
    except Exception:  # noqa: BLE001
        pass
    return None


def service_of(primitive: Any) -> Any | None:
    for obj in (primitive, getattr(primitive, "_mode", None), getattr(primitive, "mode", None)):
        if obj is None:
            continue
        for attr in ("_service", "service"):
            svc = getattr(obj, attr, None)
            if svc is not None and not callable(svc):
                return svc
    if available():
        try:
            from qiskit_ibm_runtime import QiskitRuntimeService

            return QiskitRuntimeService()
        except Exception:  # noqa: BLE001
            return None
    return None


def fetch_job(service: Any, job_id: str) -> Any | None:
    if service is None:
        return None
    try:
        return service.job(job_id)
    except Exception:  # noqa: BLE001
        return None


def job_backend_name(job: Any) -> str | None:
    try:
        b = job.backend()
        return getattr(b, "name", None) or (b if isinstance(b, str) else None)
    except Exception:  # noqa: BLE001
        return None


def calibration_time(backend: Any) -> str | None:
    try:
        props = backend.properties()
        ts = getattr(props, "last_update_date", None)
        return None if ts is None else str(ts)
    except Exception:  # noqa: BLE001
        return None
