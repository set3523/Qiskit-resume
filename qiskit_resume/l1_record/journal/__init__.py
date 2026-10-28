"""l1_record.journal: call records (CallRecord).

IN : CallRecord fields / (kind, key_hash, occurrence)
OUT: CallRecord | None, lists, status updates (submitted -> done/failed), invalidation
Depends on: l0_disk.db, l0_disk.blobs
Owns table: calls
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 2.2
"""

from qiskit_resume.l1_record.journal.core import CallKind, CallRecord, CallStatus, Journal

__all__ = ["CallKind", "CallRecord", "CallStatus", "Journal"]
