"""Questions -> deterministic byte parts; QPY I/O. See docs/CONTRACTS.md section 4.1.

Circuits are canonicalised by *structure* (instruction names, qubits, parameter
names, numeric params rounded to 12 significant digits) rather than QPY bytes,
because QPY embeds random Parameter UUIDs that change on every run.
"""

from __future__ import annotations

import hashlib
import io
import json
from typing import Any, Mapping, Sequence

import numpy as np
from qiskit import qpy
from qiskit.circuit import ParameterExpression, QuantumCircuit
from qiskit.primitives.containers.estimator_pub import EstimatorPub
from qiskit.primitives.containers.sampler_pub import SamplerPub

from qiskit_resume.l0_disk.codec import canonical_floats, canonical_scalar


def _num(x: Any) -> str:
    if isinstance(x, ParameterExpression):
        if not x.parameters:
            return _num(complex(x))
        return _expr_canonical(x)
    if isinstance(x, (bool, np.bool_)):
        return "b:" + str(bool(x))
    if isinstance(x, (int, np.integer)):
        return "i:" + str(int(x))
    if isinstance(x, (float, np.floating)):
        return "f:" + _f(float(x))
    if isinstance(x, (complex, np.complexfloating)):
        return f"c:{_f(complex(x).real)},{_f(complex(x).imag)}"
    if isinstance(x, np.ndarray):
        return "a:" + canonical_array(x).decode()
    if isinstance(x, QuantumCircuit):
        return "q:" + hashlib.sha256(circuit_canonical_bytes(x)).hexdigest()
    if isinstance(x, str):
        return "s:" + x
    return "o:" + type(x).__qualname__ + ":" + str(x)


def _probe_value(name: str, k: int) -> float:
    """Deterministic, name-derived probe value in [0.5, 1.5)."""
    h = hashlib.sha256(f"{k}:{name}".encode()).digest()
    return 0.5 + int.from_bytes(h[:8], "big") / 2.0**64


def _expr_canonical(x: ParameterExpression) -> str:
    """Canonical form of a symbolic parameter expression.

    The printed form is not stable: equal expressions print differently (``1*g`` vs ``g``) after a
    QPY round trip or assign_parameters. Instead, identify the expression by its parameter names and
    its values at two deterministic probe points (12 significant digits each).
    """
    params = sorted(x.parameters, key=lambda p: p.name)
    names = ",".join(p.name for p in params)
    vals = []
    for k in (1, 2):
        try:
            v = complex(x.bind({p: _probe_value(p.name, k) for p in params}))
            vals.append(f"{_f(v.real)},{_f(v.imag)}")
        except Exception:  # noqa: BLE001
            return "p:" + str(x)
    return "p:" + names + "@" + ";".join(vals)


def _f(x: float) -> str:
    return canonical_scalar(x)


def canonical_array(values: Any) -> bytes:
    return canonical_floats(values)


def circuit_canonical_bytes(circuit: QuantumCircuit) -> bytes:
    qidx = {q: i for i, q in enumerate(circuit.qubits)}
    cidx = {c: i for i, c in enumerate(circuit.clbits)}
    lines = [
        f"nq={circuit.num_qubits};nc={circuit.num_clbits}",
        "qregs=" + ",".join(f"{r.name}:{r.size}" for r in circuit.qregs),
        "cregs=" + ",".join(f"{r.name}:{r.size}" for r in circuit.cregs),
        "phase=" + _num(circuit.global_phase),
        "params=" + ",".join(p.name for p in circuit.parameters),
    ]
    for inst in circuit.data:
        op = inst.operation
        parts = [op.name, str(getattr(op, "num_ctrl_qubits", ""))]
        parts.append("(" + ",".join(_num(p) for p in op.params) + ")")
        parts.append("q" + ",".join(str(qidx[q]) for q in inst.qubits))
        parts.append("c" + ",".join(str(cidx[c]) for c in inst.clbits))
        cond = getattr(op, "condition", None)
        if cond is not None:
            parts.append("if" + str(cond))
        for block in getattr(op, "blocks", ()) or ():
            parts.append("{" + hashlib.sha256(circuit_canonical_bytes(block)).hexdigest() + "}")
        lines.append("|".join(parts))
    layout = getattr(circuit, "layout", None)
    if layout is not None:
        try:
            lines.append("layout=" + ",".join(map(str, layout.final_index_layout())))
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(lines).encode()


def circuit_structure_hash(circuit: QuantumCircuit) -> str:
    return hashlib.sha256(circuit_canonical_bytes(circuit)).hexdigest()


def _observables_bytes(obs: Any) -> bytes:
    items = obs.tolist()

    def canon(x: Any) -> Any:
        if isinstance(x, dict):
            return sorted((k, _num(v)) for k, v in x.items())
        return [canon(y) for y in x]

    return json.dumps({"shape": list(obs.shape), "items": canon(items)}).encode()


def question_parts(
    kind: str,
    pubs: Sequence[Any],
    *,
    precision: float | None = None,
    shots: int | None = None,
    options: Mapping[str, Any] | None = None,
) -> dict[str, bytes]:
    if isinstance(pubs, (QuantumCircuit, tuple)) or not isinstance(pubs, Sequence):
        pubs = [pubs]
    parts: dict[str, bytes] = {"kind": kind.encode()}
    for i, pub in enumerate(pubs):
        if kind == "estimator":
            p = EstimatorPub.coerce(pub, precision)
            parts[f"pub{i}.observables"] = _observables_bytes(p.observables)
            parts[f"pub{i}.precision"] = _num(p.precision).encode()
        elif kind == "sampler":
            p = SamplerPub.coerce(pub, shots)
            parts[f"pub{i}.shots"] = _num(p.shots).encode()
        else:
            raise ValueError(f"unknown primitive kind: {kind}")
        parts[f"pub{i}.circuit"] = circuit_canonical_bytes(p.circuit)
        params = p.circuit.parameters
        values = p.parameter_values.as_array(params) if len(params) else np.zeros(p.parameter_values.shape + (0,))
        parts[f"pub{i}.params"] = canonical_array(values)
    if options:
        parts["options"] = json.dumps(options, sort_keys=True, default=str).encode()
    return parts


def pub_shapes(kind: str, pubs: Sequence[Any], *, precision: float | None = None, shots: int | None = None) -> list[dict]:
    """Shape info used to build synthetic results (selftest)."""
    if isinstance(pubs, (QuantumCircuit, tuple)) or not isinstance(pubs, Sequence):
        pubs = [pubs]
    out = []
    for pub in pubs:
        if kind == "estimator":
            p = EstimatorPub.coerce(pub, precision)
            out.append({"shape": tuple(p.shape)})
        else:
            p = SamplerPub.coerce(pub, shots)
            out.append({"shape": tuple(p.shape), "shots": int(p.shots or 1024),
                        "cregs": [(r.name, r.size) for r in p.circuit.cregs]})
    return out


def circuit_to_qpy(circuit: QuantumCircuit) -> bytes:
    buf = io.BytesIO()
    qpy.dump(circuit, buf)
    return buf.getvalue()


def circuit_from_qpy(data: bytes) -> QuantumCircuit:
    return qpy.load(io.BytesIO(data))[0]


def backend_name(obj: Any) -> str | None:
    """Best-effort device name of a primitive / backend / batch / session. None for local primitives."""
    for cand in (obj, getattr(obj, "_mode", None), getattr(obj, "mode", None), getattr(obj, "_backend", None)):
        if cand is None:
            continue
        name = getattr(cand, "name", None)
        if isinstance(name, str) and name:
            return name
        b = getattr(cand, "backend", None)
        if callable(b):
            try:
                b = b()
            except Exception:  # noqa: BLE001
                b = None
        if isinstance(b, str) and b:
            return b
        if b is not None and isinstance(getattr(b, "name", None), str):
            return b.name
    return None
