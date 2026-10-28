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

    def __init__(self, inner: Any, *, backend: Any = None, factory: Any = None) -> None:
        self._pm = inner
        self._factory = factory  # builds the pass manager on first use (needs the active experiment's seed)
        self._backend_name = backend if isinstance(backend, str) or backend is None else getattr(backend, "name", None)
        self._warned = False

    @property
    def _inner(self) -> Any:
        if self._pm is None:
            self._pm = self._factory()
        return self._pm

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
        if name.startswith("_"):
            raise AttributeError(name)
        return getattr(self._inner, name)


def pass_manager(backend: Any = None, optimization_level: int | None = None, **kwargs: Any) -> WrappedPassManager:
    """One call instead of three: a recorded preset pass manager for ``backend``.

    Same as ``resume.wrap(generate_preset_pass_manager(optimization_level, backend=backend, **kwargs),
    backend=backend)`` with ``seed_transpiler`` fixed by the experiment (``resume.seed_for("transpile")``)
    unless you pass one. It is built on first use, so it can be created before ``resume_run``.
    Without a backend (e.g. ``basis_gates=[...]`` for a simulator) the cache is keyed as device "local".
    """
    from qiskit.transpiler import generate_preset_pass_manager

    from qiskit_resume.l4_api.rng import seed_for

    def build() -> Any:
        kw = dict(kwargs)
        kw.setdefault("seed_transpiler", seed_for("transpile"))
        if optimization_level is not None:
            kw["optimization_level"] = optimization_level
        if backend is not None:
            kw["backend"] = backend
        return generate_preset_pass_manager(**kw)

    return WrappedPassManager(None, backend=backend if backend is not None else "local", factory=build)
