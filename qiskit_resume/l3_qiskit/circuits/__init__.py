"""l3_qiskit.circuits: questions -> deterministic byte parts; QPY I/O.

IN : Qiskit pubs / QuantumCircuit / observables / shots / options
OUT: QuestionParts = dict[str, bytes]; circuit_to_qpy / circuit_from_qpy
Depends on: l0_disk.codec, (Qiskit)
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 4.1
"""

from qiskit_resume.l3_qiskit.circuits.core import (
    backend_name,
    canonical_array,
    circuit_canonical_bytes,
    circuit_from_qpy,
    circuit_structure_hash,
    circuit_to_qpy,
    pub_shapes,
    question_parts,
)

__all__ = [
    "backend_name",
    "canonical_array",
    "circuit_canonical_bytes",
    "circuit_from_qpy",
    "circuit_structure_hash",
    "circuit_to_qpy",
    "pub_shapes",
    "question_parts",
]
