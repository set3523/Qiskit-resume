"""l1_record.hashing: question parts -> InputKey (D-005).

IN : QuestionParts = dict[str, bytes]
OUT: InputKey(key_hash: str, parts: dict[str, str])
     normalize_floats(array) -> bytes   (12 significant digits)
Depends on: l0_disk.codec
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 2.1
"""

from qiskit_resume.l1_record.hashing.core import (
    HASH_VERSION,
    InputKey,
    diff_parts,
    digest_parts,
    normalize_floats,
)

__all__ = ["HASH_VERSION", "InputKey", "diff_parts", "digest_parts", "normalize_floats"]
