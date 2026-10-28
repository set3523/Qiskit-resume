"""resume.wrap(x): one entry point for everything worth protecting (D-003). See docs/CONTRACTS.md section 5.3."""

from __future__ import annotations

from typing import Any

from qiskit_resume.l4_api.function import WrappedFunction
from qiskit_resume.l4_api.passmanager import WrappedPassManager
from qiskit_resume.l4_api.primitive import WrappedEstimator, WrappedSampler


def wrap(obj: Any = None, *, name: str | None = None, allow_pickle: bool = False, backend: Any = None) -> Any:
    """Protect one object; the single entry point of qiskit-resume.

    resume.wrap(estimator)            an Estimator (V2, any kind): each run() call is recorded and
    resume.wrap(sampler)              replayed / reconnected on reruns. Attributes such as .options
                                      pass through to the real primitive.
    resume.wrap(pm, backend=backend)  a pass manager: transpiled circuits are recorded per (source
                                      circuit, device). resume.pass_manager() builds one for you.
    @resume.wrap                      a slow, deterministic, side-effect-free function: its return
    def f(...): ...                   value is recorded per arguments, so reruns skip the work.
    @resume.wrap(name="...", allow_pickle=True)
                                      name: record key (default: the function's qualified name);
                                      allow_pickle: also store return values that are not plain
                                      numbers / numpy arrays / SparsePauliOp / QuantumCircuit (pickle; trusted files only).
    Wrapping twice returns the same object. Anything else raises TypeError.
    """
    if obj is None:  # used as @resume.wrap(allow_pickle=True)
        return lambda f: wrap(f, name=name, allow_pickle=allow_pickle, backend=backend)
    if getattr(obj, "_qresume_wrapped", False):
        return obj
    from qiskit.passmanager import BasePassManager
    from qiskit.primitives import BaseEstimatorV2, BaseSamplerV2

    if isinstance(obj, BaseEstimatorV2):
        return WrappedEstimator(obj)
    if isinstance(obj, BaseSamplerV2):
        return WrappedSampler(obj)
    if isinstance(obj, BasePassManager):
        return WrappedPassManager(obj, backend=backend)
    if callable(obj) and not isinstance(obj, type):
        return WrappedFunction(obj, name=name, allow_pickle=allow_pickle)
    raise TypeError(
        f"resume.wrap cannot protect {type(obj).__name__}: pass an Estimator/Sampler (V2), "
        "a pass manager, or a function")
