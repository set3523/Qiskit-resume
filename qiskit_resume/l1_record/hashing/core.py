"""Question parts -> InputKey (D-005). See docs/CONTRACTS.md section 2.1."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Mapping

from qiskit_resume.l0_disk.codec import canonical_floats

HASH_VERSION = 1
_PREFIX = b"qiskit-resume/input-key/v%d\x00" % HASH_VERSION


@dataclass(frozen=True)
class InputKey:
    key_hash: str
    parts: dict[str, str] = field(default_factory=dict)


def digest_parts(parts: Mapping[str, bytes]) -> InputKey:
    if not parts:
        raise ValueError("question has no parts")
    part_hashes: dict[str, str] = {}
    for name, data in parts.items():
        if not isinstance(name, str) or not name:
            raise ValueError(f"invalid part name: {name!r}")
        if not isinstance(data, (bytes, bytearray)):
            raise TypeError(f"part {name!r} must be bytes, got {type(data).__name__}")
        part_hashes[name] = hashlib.sha256(bytes(data)).hexdigest()
    h = hashlib.sha256(_PREFIX)
    for name in sorted(part_hashes):
        nb = name.encode("utf-8")
        h.update(len(nb).to_bytes(4, "little"))
        h.update(nb)
        h.update(bytes.fromhex(part_hashes[name]))
    return InputKey(h.hexdigest(), dict(sorted(part_hashes.items())))


def normalize_floats(values: Any, *, sig_digits: int = 12) -> bytes:
    """See l0_disk.codec.canonical_floats (single implementation shared with l3)."""
    return canonical_floats(values, sig_digits=sig_digits)


def diff_parts(a: InputKey, b: InputKey) -> list[str]:
    names = set(a.parts) | set(b.parts)
    return sorted(n for n in names if a.parts.get(n) != b.parts.get(n))
