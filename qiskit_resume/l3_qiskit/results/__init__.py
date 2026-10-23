"""l3_qiskit.results: PrimitiveResult <-> neutral format (D-013).

IN : PrimitiveResult | NeutralResult
OUT: the other one (NeutralResult is a codec-storable dict with a format version)
Depends on: l0_disk.codec, (Qiskit)
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 4.2
"""

from qiskit_resume.l3_qiskit.results.core import NEUTRAL_VERSION, UnsupportedResultError, from_neutral, to_neutral

__all__ = ["NEUTRAL_VERSION", "UnsupportedResultError", "from_neutral", "to_neutral"]
