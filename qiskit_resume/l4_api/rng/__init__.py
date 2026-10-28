"""l4_api.rng: resume.seed / resume.rng(), global RNG seeding, RNG state capture/restore (D-006).

IN : recorded seed
OUT: int seed (also per name: seed_for), numpy Generator (by call order, by name, or by explicit seed), rng state bytes
Depends on: l0_disk.codec
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 5.2
"""

from qiskit_resume.l4_api.rng.core import (
    NoActiveRunError,
    activate,
    apply_global_seed,
    capture_state,
    deactivate,
    restore_state,
    rng,
    seed,
    seed_for,
)

__all__ = [
    "NoActiveRunError",
    "activate",
    "apply_global_seed",
    "capture_state",
    "deactivate",
    "restore_state",
    "rng",
    "seed",
    "seed_for",
]
