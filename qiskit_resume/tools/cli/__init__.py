"""tools.cli: qresume status/check/selftest/copy/export/pack/unpack/quota/fork/compact (D-012, D-014).

Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 6.1
"""

from qiskit_resume.tools.cli.core import build_parser, main

__all__ = ["build_parser", "main"]
