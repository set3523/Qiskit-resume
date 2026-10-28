"""l4_api.snapshot: run.snapshot(state) / run.load_snapshot() (D-011).

IN : state dict
OUT: state dict | None (RNG state included automatically)
Depends on: l0 codec, l4 rng
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 5.7
"""

from qiskit_resume.l4_api.snapshot.core import load, save

__all__ = ["load", "save"]
