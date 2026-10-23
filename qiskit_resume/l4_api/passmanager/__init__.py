"""l4_api.passmanager: transpile cache (source circuit + device -> QPY) (D-005, D-007).

IN : source circuit
OUT: transpiled circuit
Depends on: l1, l2 replay, l3 circuits, l4 run
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 5.5
"""

from qiskit_resume.l4_api.passmanager.core import WrappedPassManager

__all__ = ["WrappedPassManager"]
