"""qiskit-resume: record & replay for Qiskit Primitives workflows.

Typical use::

    import qiskit_resume as resume

    estimator = resume.wrap(EstimatorV2(mode=backend))
    with resume.resume_run("vqe.qresume"):
        ...  # plain Qiskit code; rerun the same script after an interruption

Design: docs/DECISIONS.md | structure: docs/ARCHITECTURE.md | contracts: docs/CONTRACTS.md
"""

from typing import Any

from qiskit_resume._version import __version__
from qiskit_resume.l0_disk.codec import EncodeError
from qiskit_resume.l0_disk.db import AlreadyRunningError, IntegrityError
from qiskit_resume.l1_record.runs import ImmutableSettingError
from qiskit_resume.l4_api.primitive import BudgetExceeded, JobWaitTimeout, QuotaExhausted
from qiskit_resume.l4_api.rng import NoActiveRunError, rng
from qiskit_resume.l4_api.run import BackendChangedStop, DivergenceStop, ResumeRun, get_active_run, resume_run
from qiskit_resume.l4_api.wrap import wrap

__all__ = [
    "AlreadyRunningError",
    "BackendChangedStop",
    "BudgetExceeded",
    "DivergenceStop",
    "EncodeError",
    "ImmutableSettingError",
    "IntegrityError",
    "JobWaitTimeout",
    "NoActiveRunError",
    "QuotaExhausted",
    "ResumeRun",
    "__version__",
    "get_active_run",
    "resume_run",
    "rng",
    "seed",
    "wrap",
]


def __getattr__(name: str) -> Any:
    if name == "seed":  # resume.seed -> the active experiment's seed
        from qiskit_resume.l4_api.rng import seed as _seed

        return _seed()
    raise AttributeError(f"module 'qiskit_resume' has no attribute {name!r}")
