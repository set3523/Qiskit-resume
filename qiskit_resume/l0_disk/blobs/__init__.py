"""l0_disk.blobs: content-addressed blob store (D-013 dedup).

IN : bytes, kind
OUT: BlobRef (str, SHA-256 hex); get(BlobRef) -> bytes; has(BlobRef) -> bool
     content_hash(bytes) -> str
Depends on: l0_disk.db
Owns table: blobs
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 1.2
"""

from qiskit_resume.l0_disk.blobs.core import BlobStore, content_hash

__all__ = ["BlobStore", "content_hash"]
