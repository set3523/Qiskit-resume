"""Record & replay a user function's return value (D-011). See docs/CONTRACTS.md section 5.6."""

from __future__ import annotations

import functools
import hashlib
import inspect
import time
from typing import Any, Callable

from qiskit_resume.l0_disk.codec import EncodeError, decode, encode
from qiskit_resume.l1_record.hashing import digest_parts
from qiskit_resume.l3_qiskit.circuits import circuit_canonical_bytes, circuit_from_qpy, circuit_to_qpy
from qiskit_resume.l4_api.run import get_active_run

_TAG = "qiskit_resume.type"


def _to_storable(v: Any, *, for_key: bool) -> Any:
    """Turn the Qiskit objects slow classical steps usually return (SparsePauliOp, QuantumCircuit)
    into codec-storable data. For record keys, circuits use their structural form (QPY embeds random ids)."""
    from qiskit.circuit import QuantumCircuit
    from qiskit.quantum_info import SparsePauliOp

    if isinstance(v, SparsePauliOp) and not v.parameters:
        return {_TAG: "SparsePauliOp", "labels": [str(p) for p in v.paulis], "coeffs": v.coeffs}
    if isinstance(v, QuantumCircuit):
        if for_key:
            return {_TAG: "QuantumCircuit", "canonical": circuit_canonical_bytes(v)}
        return {_TAG: "QuantumCircuit", "qpy": circuit_to_qpy(v)}
    if isinstance(v, dict):
        return {k: _to_storable(x, for_key=for_key) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        out = [_to_storable(x, for_key=for_key) for x in v]
        return out if isinstance(v, list) else tuple(out)
    return v


def _from_storable(v: Any) -> Any:
    if isinstance(v, dict):
        tag = v.get(_TAG)
        if tag == "SparsePauliOp":
            from qiskit.quantum_info import SparsePauliOp

            return SparsePauliOp(list(v["labels"]), v["coeffs"])
        if tag == "QuantumCircuit":
            return circuit_from_qpy(v["qpy"])
        return {k: _from_storable(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_from_storable(x) for x in v]
    if isinstance(v, tuple):
        return tuple(_from_storable(x) for x in v)
    return v


def _identity(fn: Callable[..., Any], name: str | None) -> str:
    ident = name or f"{getattr(fn, '__module__', '?')}.{getattr(fn, '__qualname__', repr(fn))}"
    try:
        src = inspect.getsource(fn)
    except (OSError, TypeError):
        src = ""
    return ident + "#" + hashlib.sha256(src.encode()).hexdigest()[:16]


class WrappedFunction:
    _qresume_wrapped = True

    def __init__(self, fn: Callable[..., Any], *, name: str | None = None, allow_pickle: bool = False) -> None:
        self._fn = fn
        self._ident = _identity(fn, name)
        self._allow_pickle = allow_pickle
        functools.update_wrapper(self, fn)

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        run = get_active_run()
        try:
            arg_bytes = encode(_to_storable({"args": list(args), "kwargs": dict(sorted(kwargs.items()))},
                                            for_key=True), allow_pickle=self._allow_pickle)
        except EncodeError as exc:
            raise EncodeError(exc.path.replace("value", f"{self._ident.split('#')[0]} arguments", 1), None,
                              "wrapped function arguments must be storable") from exc
        key = digest_parts({"kind": b"function", "fn": self._ident.encode(), "args": arg_bytes})
        with run.db.lock:
            d = run._decide("function", key)
        if d.action == "replay":
            value = _from_storable(decode(run.blobs.get(d.record.result_ref)))
        else:
            if d.record is not None:
                run.journal.invalidate([d.record.id])
            t0 = time.perf_counter()
            value = self._fn(*args, **kwargs)
            duration = time.perf_counter() - t0
            data = encode(_to_storable(value, for_key=False), allow_pickle=self._allow_pickle)  # names the bad element
            rec = run.journal.record_submitted(run_id=run.run_id, seq=d.position, kind="function", key=key,
                                               occurrence=d.occurrence)
            with run.db.tx():
                ref = run.blobs.put(data, kind="value")
            run.journal.record_done(rec.id, result_ref=ref, duration_s=duration)
        run._mark_done()
        return value

    def __get__(self, obj: Any, objtype: Any = None) -> Any:  # support methods
        return self if obj is None else functools.partial(self.__call__, obj)
