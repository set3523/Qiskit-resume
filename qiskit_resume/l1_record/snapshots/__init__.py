"""l1_record.snapshots: snapshot records (SnapshotRecord).

IN : run_id, position, state bytes, rng bytes
OUT: SnapshotRecord, latest valid snapshot, invalidation
Depends on: l0_disk.db, l0_disk.blobs
Owns table: snapshots
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 2.3
"""

from qiskit_resume.l1_record.snapshots.core import SnapshotRecord, SnapshotStore

__all__ = ["SnapshotRecord", "SnapshotStore"]
