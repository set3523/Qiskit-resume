"""l4_api.device: resume.backend() - pick an IBM device once and reuse it on every rerun (D-007, D-020).

IN : optional QiskitRuntimeService, optional device name
OUT: backend object; the device name is recorded in the experiment file
Depends on: l4 run
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 5.8
"""

from qiskit_resume.l4_api.device.core import backend

__all__ = ["backend"]
