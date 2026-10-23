"""l0_disk.codec: value <-> bytes (D-013).

IN : Python value (None/bool/int/float/complex/str/bytes/list/tuple/dict[str, ...]/numpy)
OUT: bytes; decode(bytes) -> value
     canonical_floats(array) -> bytes  12-significant-digit text used as hash input (shared by l1 and l3)
Errors: EncodeError (with the path of the unstorable value, raised at record time)
Option: allow_pickle=True (fragile across library versions)
Depends on: nothing
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 1.3
"""

from qiskit_resume.l0_disk.codec.core import (
    EncodeError,
    canonical_floats,
    canonical_scalar,
    decode,
    encode,
)

__all__ = ["EncodeError", "canonical_floats", "canonical_scalar", "decode", "encode"]
