"""l0_disk.db: the .qresume SQLite file (D-004, D-012, D-014).

IN : file path
OUT: ExperimentDB (tx(), get_meta/set_meta, ensure_schema(ddl), integrity_check(), close())
     backup_copy(src, dst) -> Path      consistent copy, safe while running
     verify_file(path)                  IntegrityError if damaged
     running_info(path) -> dict | None  {host, pid, started_at} if a run holds the file
Errors: AlreadyRunningError, IntegrityError, FormatError
Depends on: nothing
Owns table: meta
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 1.1
"""

from qiskit_resume.l0_disk.db.core import (
    FORMAT_VERSION,
    AlreadyRunningError,
    ExperimentDB,
    FormatError,
    IntegrityError,
    backup_copy,
    running_info,
    utc_now,
    verify_file,
)

__all__ = [
    "FORMAT_VERSION",
    "AlreadyRunningError",
    "ExperimentDB",
    "FormatError",
    "IntegrityError",
    "backup_copy",
    "running_info",
    "utc_now",
    "verify_file",
]
