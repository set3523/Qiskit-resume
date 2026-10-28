"""l4_api.passmanager: transpile cache (source circuit + device -> QPY) (D-005, D-007).

IN : source circuit (or, for pass_manager(), a backend + preset options)
OUT: transpiled circuit; pass_manager() -> a recorded preset pass manager
Depends on: l1, l2 replay, l3 circuits, l4 run, l4 rng
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 5.5
"""

from qiskit_resume.l4_api.passmanager.core import WrappedPassManager, pass_manager

__all__ = ["WrappedPassManager", "pass_manager"]
