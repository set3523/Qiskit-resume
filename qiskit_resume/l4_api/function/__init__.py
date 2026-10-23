"""l4_api.function: record and replay a function's return value (D-011).

IN : function arguments
OUT: return value
Depends on: l0 codec, l1, l2 replay, l4 run
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 5.6
"""

from qiskit_resume.l4_api.function.core import WrappedFunction

__all__ = ["WrappedFunction"]
