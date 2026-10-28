"""tools.selftest: crash and resume a script at several points with synthetic results, compare (D-012).

Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 6.2
"""

from qiskit_resume.tools.selftest.core import SelftestPoint, SelftestReport, report_json, report_text, run_selftest

__all__ = ["SelftestPoint", "SelftestReport", "report_json", "report_text", "run_selftest"]
