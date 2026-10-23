"""l4_api.primitive: wraps Estimator / Sampler run() (D-005, D-008).

IN : pubs, options
OUT: run() -> job, job.result() -> PrimitiveResult
Depends on: l1 hashing/journal, l2 replay/budget, l3 circuits/results/ibm, l4 run
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 5.4
"""

from qiskit_resume.l4_api.primitive.core import (
    BudgetExceeded,
    JobWaitTimeout,
    QuotaExhausted,
    WrappedEstimator,
    WrappedJob,
    WrappedPrimitive,
    WrappedSampler,
)

__all__ = ["BudgetExceeded", "JobWaitTimeout", "QuotaExhausted", "WrappedEstimator", "WrappedJob", "WrappedPrimitive", "WrappedSampler"]
