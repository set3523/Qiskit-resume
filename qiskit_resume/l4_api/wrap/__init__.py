"""l4_api.wrap: resume.wrap(x) dispatch: primitive / pass manager / function (D-003).

IN : user object
OUT: wrapped object
Depends on: l4_api.primitive, l4_api.passmanager, l4_api.function
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 5.3
"""

from qiskit_resume.l4_api.wrap.core import wrap

__all__ = ["wrap"]
