"""l2_replay.divergence: why a replayed question differs and what to do (D-006, D-012, D-014).

IN : Divergence, code changed?, started from snapshot?, force, on_divergence
OUT: Verdict(level, message, action = continue | stop)
Depends on: l1_record.journal, l1_record.runs, l2_replay.replay
Contract (signatures, types, errors, invariants): docs/CONTRACTS.md section 3.2
"""

from qiskit_resume.l2_replay.divergence.core import Verdict, apply, decide

__all__ = ["Verdict", "apply", "decide"]
