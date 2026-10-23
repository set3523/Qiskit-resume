"""Record & replay a user function's return value (D-011). See docs/CONTRACTS.md section 5.6."""

from __future__ import annotations

import functools
import hashlib
import inspect
import time
from typing import Any, Callable

from qiskit_resume.l0_disk.codec import EncodeError, decode, encode
from qiskit_resume.l1_record.hashing import digest_parts
from qiskit_resume.l4_api.run import get_active_run


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
            arg_bytes = encode({"args": list(args), "kwargs": dict(sorted(kwargs.items()))},
                               allow_pickle=self._allow_pickle)
        except EncodeError as exc:
            raise EncodeError(exc.path.replace("value", f"{self._ident.split('#')[0]} arguments", 1), None,
                              "wrapped function arguments must be storable") from exc
        key = digest_parts({"kind": b"function", "fn": self._ident.encode(), "args": arg_bytes})
        with run.db.lock:
            d = run._decide("function", key)
        if d.action == "replay":
            value = decode(run.blobs.get(d.record.result_ref))
        else:
            if d.record is not None:
                run.journal.invalidate([d.record.id])
            t0 = time.perf_counter()
            value = self._fn(*args, **kwargs)
            duration = time.perf_counter() - t0
            data = encode(value, allow_pickle=self._allow_pickle)  # EncodeError names the bad element
            rec = run.journal.record_submitted(run_id=run.run_id, seq=d.position, kind="function", key=key,
                                               occurrence=d.occurrence)
            with run.db.tx():
                ref = run.blobs.put(data, kind="value")
            run.journal.record_done(rec.id, result_ref=ref, duration_s=duration)
        run._mark_done()
        return value

    def __get__(self, obj: Any, objtype: Any = None) -> Any:  # support methods
        return self if obj is None else functools.partial(self.__call__, obj)
