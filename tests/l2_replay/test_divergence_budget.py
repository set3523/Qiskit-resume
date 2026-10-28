from qiskit_resume.l0_disk.db import utc_now
from qiskit_resume.l1_record.hashing import digest_parts
from qiskit_resume.l1_record.runs import RunLog
from qiskit_resume.l2_replay.budget import Budget
from qiskit_resume.l2_replay.divergence import apply, decide
from qiskit_resume.l2_replay.replay import Divergence


def _div(journal):
    rec = journal.record_submitted(run_id=1, seq=0, kind="estimator", key=digest_parts({"a": b"1"}), occurrence=0)
    return Divergence(0, "estimator", rec, digest_parts({"a": b"2"}), ["a"])


def test_decision_table(journal):
    d = _div(journal)
    assert decide(d, code_changed=False, started_from_snapshot=True, force=True).action == "continue"
    v = decide(d, code_changed=True, started_from_snapshot=True, force=False)
    assert (v.level, v.action, v.invalidate_from) == ("warning", "continue", 0)
    assert decide(d, code_changed=True, started_from_snapshot=False, force=False, on_divergence="stop").action == "stop"
    v = decide(d, code_changed=False, started_from_snapshot=True, force=False)
    assert (v.level, v.action) == ("error", "stop") and "force=True" in v.message
    v = decide(d, code_changed=False, started_from_snapshot=False, force=False)
    assert v.level == "warning" and "resume.rng(" in v.message


def test_apply_invalidates_and_logs(db, journal):
    runs = RunLog(db)
    d = _div(journal)
    v = decide(d, code_changed=False, started_from_snapshot=False, force=True)
    assert apply(v, d, journal=journal, runs=runs, run_id=1) == [d.expected.id]
    assert [e.kind for e in runs.events()] == ["force_branch", "calls_invalidated"]


def test_budget_summary_window(db, blobs, journal):
    k = digest_parts({"pub0.circuit": b"c"})
    with db.tx():
        ref = blobs.put(b"r")
    for run_id, usage in ((1, 10.0), (2, 5.0)):
        r = journal.record_submitted(run_id=run_id, seq=run_id, kind="estimator", key=k, occurrence=run_id, job_id="j")
        journal.record_done(r.id, result_ref=ref, usage_s=usage)
    b = Budget(journal, run_id=2, limit_s=6.0)
    s = b.summary()
    assert (s.used_window_s, s.used_run_s, s.remaining_s) == (15.0, 5.0, 1.0)
    assert s.next_recovery_at > utc_now()
    assert b.estimate("estimator", k) == 1.2 * 7.5 and not b.can_submit(9.0) and b.can_submit(None)
    assert Budget(journal, run_id=2, limit_s=None).can_submit(1e9)
