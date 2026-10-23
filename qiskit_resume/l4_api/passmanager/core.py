"""Transpile cache: (source circuit, device) -> recorded transpiled circuit (D-005, D-007).

See docs/CONTRACTS.md section 5.5.
"""

from __future__ import annotations

import json
import time
from typing import Any

from qiskit.circuit import QuantumCircuit

from qiskit_resume.l1_record.hashing import digest_parts
from qiskit_resume.l3_qiskit.circuits import circuit_canonical_bytes, circuit_from_qpy, circuit_to_qpy
from qiskit_resume.l4_api.run import get_active_run


def _rebind_parameters(out: QuantumCircuit, like: QuantumCircuit) -> QuantumCircuit:
    """A circuit loaded from QPY has its own Parameter objects; map them back to the user's by name."""
    mine = {p.name: p for p in like.parameters}
    mapping = {p: mine[p.name] for p in out.parameters if p.name in mine and mine[p.name] is not p}
    return out.assign_parameters(mapping) if mapping else out


class WrappedPassManager:
    _qresume_wrapped = True

    def __init__(self, inner: Any, *, backend: Any = None) -> None:
        self._inner = inner
        self._backend_name = backend if isinstance(backend, str) or backend is None else getattr(backend, "name", None)
        self._warned = False

    def run(self, circuits: Any, **kwargs: Any) -> Any:
        single = isinstance(circuits, QuantumCircuit)
        items = [circuits] if single else list(circuits)
        out = [self._one(qc, kwargs) for qc in items]
        return out[0] if single else out

    def _one(self, qc: QuantumCircuit, kwargs: dict[str, Any]) -> QuantumCircuit:
        run = get_active_run()
        if self._backend_name is None and not self._warned:
            self._warned = True
            run._say("warning", "transpile cache does not know the device; use resume.wrap(pm, backend=backend) "
                                "so a device change triggers a fresh transpile")
        parts = {
            "kind": b"transpile",
            "circuit": circuit_canonical_bytes(qc),
            "backend": (self._backend_name or "").encode(),
            "pass_manager": type(self._inner).__qualname__.encode(),
            "kwargs": json.dumps(kwargs, sort_keys=True, default=str).encode(),
        }
        key = digest_parts(parts)
        with run.db.lock:
            d = run._decide("transpile", key)
        if d.action == "replay":
            result = _rebind_parameters(circuit_from_qpy(run.blobs.get(d.record.result_ref)), qc)
        else:
            if d.record is not None:
                run.journal.invalidate([d.record.id])
            t0 = time.perf_counter()
            result = self._inner.run(qc, **kwargs)
            rec = run.journal.record_submitted(run_id=run.run_id, seq=d.position, kind="transpile", key=key,
                                               occurrence=d.occurrence, backend=self._backend_name)
            with run.db.tx():
                ref = run.blobs.put(circuit_to_qpy(result), kind="circuit_qpy")
            run.journal.record_done(rec.id, result_ref=ref, duration_s=time.perf_counter() - t0)
        run._mark_done()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
