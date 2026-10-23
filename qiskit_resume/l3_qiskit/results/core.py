"""PrimitiveResult <-> neutral format (D-013). See docs/CONTRACTS.md section 4.2.

The neutral format contains only plain values and numpy arrays, so stored files
do not depend on Qiskit's class layout. Only this module changes when Qiskit does.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import qiskit
from qiskit.primitives.containers import BitArray, DataBin, PrimitiveResult, PubResult, SamplerPubResult

NEUTRAL_VERSION = 1


class UnsupportedResultError(ValueError):
    pass


def _plain(x: Any) -> Any:
    """Make metadata storable (Codable); unknown objects become their str()."""
    if x is None or isinstance(x, (bool, int, float, complex, str, bytes, np.ndarray, np.generic)):
        return x
    if isinstance(x, (list, tuple)):
        return [_plain(v) for v in x]
    if isinstance(x, dict):
        return {str(k): _plain(v) for k, v in x.items()}
    return str(x)


def _data_items(data: DataBin) -> list[tuple[str, Any]]:
    try:
        return list(data.items())
    except AttributeError:  # older Qiskit
        return [(k, getattr(data, k)) for k in data._FIELDS]  # type: ignore[attr-defined]


def to_neutral(result: PrimitiveResult, *, kind: str, sampler_storage: str = "raw") -> dict[str, Any]:
    pubs = []
    for pub in result:
        fields: dict[str, Any] = {}
        for name, value in _data_items(pub.data):
            if isinstance(value, BitArray):
                if sampler_storage == "counts" and value.shape == ():
                    fields[name] = {"type": "counts", "counts": dict(value.get_counts()), "num_bits": value.num_bits}
                else:
                    fields[name] = {"type": "bitarray", "bits": np.asarray(value.array), "num_bits": value.num_bits}
            elif isinstance(value, np.ndarray) or np.isscalar(value):
                fields[name] = {"type": "array", "value": np.asarray(value)}
            else:
                raise UnsupportedResultError(f"unsupported result field {name!r}: {type(value).__name__}")
        pubs.append({"shape": list(getattr(pub.data, "shape", ())), "metadata": _plain(dict(pub.metadata)),
                     "data": fields, "sampler": isinstance(pub, SamplerPubResult)})
    return {"v": NEUTRAL_VERSION, "kind": kind, "qiskit_version": qiskit.__version__,
            "metadata": _plain(dict(result.metadata)), "pubs": pubs}


def from_neutral(n: dict[str, Any]) -> PrimitiveResult:
    if int(n.get("v", 0)) > NEUTRAL_VERSION:
        raise UnsupportedResultError(f"result format v{n.get('v')} is newer than supported v{NEUTRAL_VERSION}")
    pubs = []
    for p in n["pubs"]:
        fields: dict[str, Any] = {}
        for name, f in p["data"].items():
            if f["type"] == "array":
                fields[name] = np.asarray(f["value"])
            elif f["type"] == "bitarray":
                fields[name] = BitArray(np.asarray(f["bits"], dtype=np.uint8), int(f["num_bits"]))
            elif f["type"] == "counts":
                fields[name] = BitArray.from_counts(f["counts"], int(f["num_bits"]))
            else:
                raise UnsupportedResultError(f"unknown field type {f['type']!r}")
        data = DataBin(shape=tuple(p.get("shape", ())), **fields)
        cls = SamplerPubResult if p.get("sampler") or n["kind"] == "sampler" else PubResult
        pubs.append(cls(data, metadata=p.get("metadata") or {}))
    return PrimitiveResult(pubs, metadata=n.get("metadata") or {})
