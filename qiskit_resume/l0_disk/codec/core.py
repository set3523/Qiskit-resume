"""Binary value codec for user values (wrapped-function results, snapshots) and
our own neutral records (D-013).

Layout of an encoded value::

    b"QRV1" | u32 header_len | header (UTF-8 JSON) | segment bytes ...

The JSON header holds the structure. Numpy arrays (and, if allowed, pickles)
are stored as raw binary segments and referenced from the header, so numbers
inside arrays never go through text. Python floats in the header use ``repr``
which round-trips exactly.

Supported without pickle: None, bool, int, float, complex, str, bytes,
list, tuple, dict (str keys), numpy arrays and numpy scalars.
Anything else raises :class:`EncodeError` at encode time (fail fast, B7),
with the path to the offending value.
"""

from __future__ import annotations

import json
import pickle
import struct
from typing import Any

import numpy as np

MAGIC = b"QRV1"
_HDR = struct.Struct("<I")


class EncodeError(TypeError):
    """A value cannot be stored. ``path`` points at the offending element."""

    def __init__(self, path: str, value: Any, hint: str = "") -> None:
        self.path = path
        msg = f"cannot store value at {path}: {type(value).__module__}.{type(value).__qualname__}"
        if hint:
            msg += f" - {hint}"
        super().__init__(msg)


_DEFAULT_HINT = (
    "return plain numbers/lists/dicts or numpy arrays instead "
    "(or opt in with allow_pickle=True, which may break across library versions)"
)


def encode(value: Any, *, allow_pickle: bool = False) -> bytes:
    segments: list[bytes] = []

    def add_segment(data: bytes) -> int:
        segments.append(data)
        return len(segments) - 1

    def walk(v: Any, path: str) -> Any:
        if v is None or isinstance(v, (bool, str)):
            return v
        if isinstance(v, int) and not isinstance(v, bool):
            return v
        if isinstance(v, float):
            if v != v or v in (float("inf"), float("-inf")):
                return {"__f__": repr(v)}
            return v
        if isinstance(v, complex):
            return {"__c__": [v.real, v.imag]}
        if isinstance(v, (bytes, bytearray, memoryview)):
            return {"__b__": add_segment(bytes(v))}
        if isinstance(v, np.ndarray):
            if v.dtype.hasobject:
                if allow_pickle:
                    return {"__p__": add_segment(pickle.dumps(v, protocol=4))}
                raise EncodeError(path, v, "object-dtype arrays are not supported; " + _DEFAULT_HINT)
            arr = v if v.flags.c_contiguous else v.copy(order="C")  # keeps 0-d shape
            return {
                "__nd__": add_segment(arr.tobytes(order="C")),
                "dtype": arr.dtype.str,
                "shape": list(arr.shape),
            }
        if isinstance(v, np.generic):
            return walk(np.asarray(v), path)  # 0-d array keeps the exact dtype
        if isinstance(v, tuple):
            return {"__t__": [walk(x, f"{path}[{i}]") for i, x in enumerate(v)]}
        if isinstance(v, list):
            return [walk(x, f"{path}[{i}]") for i, x in enumerate(v)]
        if isinstance(v, dict):
            out = {}
            for k, x in v.items():
                if not isinstance(k, str):
                    raise EncodeError(f"{path}.<key {k!r}>", k, "dict keys must be str")
                if k.startswith("__") and k.endswith("__"):
                    raise EncodeError(f"{path}.{k}", k, "dunder keys are reserved")
                out[k] = walk(x, f"{path}.{k}")
            return out
        if allow_pickle:
            try:
                return {"__p__": add_segment(pickle.dumps(v, protocol=4))}
            except Exception as exc:  # noqa: BLE001
                raise EncodeError(path, v, f"pickle failed: {exc}") from exc
        raise EncodeError(path, v, _DEFAULT_HINT)

    header_obj = {"v": walk(value, "value"), "seg": None}
    header_obj["seg"] = [len(s) for s in segments]
    header = json.dumps(header_obj, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return MAGIC + _HDR.pack(len(header)) + header + b"".join(segments)


def decode(data: bytes) -> Any:
    if data[:4] != MAGIC:
        raise ValueError("not a qiskit-resume encoded value (bad magic)")
    (hlen,) = _HDR.unpack_from(data, 4)
    start = 4 + _HDR.size
    header = json.loads(data[start : start + hlen].decode("utf-8"))
    pos = start + hlen
    segs: list[bytes] = []
    for n in header["seg"]:
        segs.append(data[pos : pos + n])
        pos += n

    def walk(v: Any) -> Any:
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            if "__nd__" in v:
                arr = np.frombuffer(segs[v["__nd__"]], dtype=np.dtype(v["dtype"]))
                return arr.reshape(tuple(v["shape"])).copy()
            if "__t__" in v:
                return tuple(walk(x) for x in v["__t__"])
            if "__c__" in v:
                return complex(*v["__c__"])
            if "__f__" in v:
                return float(v["__f__"])
            if "__b__" in v:
                return segs[v["__b__"]]
            if "__p__" in v:
                return pickle.loads(segs[v["__p__"]])  # only written when allow_pickle=True
            return {k: walk(x) for k, x in v.items()}
        return v

    return walk(header["v"])


def _fmt_sig(x: float, sig: int) -> str:
    if x != x:
        return "nan"
    if x in (float("inf"), float("-inf")):
        return "inf" if x > 0 else "-inf"
    if x == 0.0:
        return "0"
    return format(x, f".{sig - 1}e")


def canonical_floats(values: Any, *, sig_digits: int = 12) -> bytes:
    """Canonical bytes of a real/complex array rounded to ``sig_digits`` significant digits (D-005).

    Decimal text is exact and platform independent, so last-bit differences between
    machines disappear while real changes (> 1e-12 relative) are kept. Shape is included.
    """
    arr = np.asarray(values)
    if np.iscomplexobj(arr):
        items = [f"{_fmt_sig(float(z.real), sig_digits)}{_fmt_sig(float(z.imag), sig_digits)}j"
                 for z in arr.astype(np.complex128).ravel()]
    else:
        items = [_fmt_sig(float(x), sig_digits) for x in arr.astype(np.float64).ravel()]
    return ("[" + "x".join(map(str, arr.shape)) + "]" + ",".join(items)).encode()


def canonical_scalar(x: float, *, sig_digits: int = 12) -> str:
    return _fmt_sig(float(x), sig_digits)
