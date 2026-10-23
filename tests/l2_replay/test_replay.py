from qiskit_resume.l1_record.hashing import digest_parts
from qiskit_resume.l2_replay.replay import ReplayEngine


def key(s):
    return digest_parts({"q": s.encode()})


def record_run(db, blobs, journal, questions):
    eng = ReplayEngine(journal, run_id=1)
    with db.tx():
        ref = blobs.put(b"r", kind="result")
    for q in questions:
        d = eng.next("estimator", key(q))
        assert d.action == "execute"
        rec = journal.record_submitted(run_id=1, seq=d.position, kind="estimator", key=key(q), occurrence=d.occurrence)
        journal.record_done(rec.id, result_ref=ref)


def test_replay_then_execute_at_end(db, blobs, journal):
    record_run(db, blobs, journal, ["a", "b", "a"])
    eng = ReplayEngine(journal, run_id=2)
    acts = [eng.next("estimator", key(q)) for q in ["a", "b", "a", "c"]]
    assert [d.action for d in acts] == ["replay", "replay", "replay", "execute"]
    assert [d.occurrence for d in acts] == [0, 0, 1, 0]
    assert acts[3].divergence is None


def test_reattach_and_failed(db, blobs, journal):
    journal.record_submitted(run_id=1, seq=0, kind="estimator", key=key("a"), occurrence=0, job_id="J")
    r = journal.record_submitted(run_id=1, seq=1, kind="estimator", key=key("b"), occurrence=0, job_id="K")
    journal.record_failed(r.id, error="device")
    eng = ReplayEngine(journal, run_id=2)
    assert eng.next("estimator", key("a")).action == "reattach"
    d = eng.next("estimator", key("b"))
    assert d.action == "execute" and d.record.id == r.id


def test_divergence_reports_changed_parts(db, blobs, journal):
    record_run(db, blobs, journal, ["a", "b"])
    eng = ReplayEngine(journal, run_id=2)
    eng.next("estimator", key("a"))
    d = eng.next("estimator", digest_parts({"q": b"b", "extra": b"x"}))
    assert d.action == "execute" and d.divergence is not None
    assert d.divergence.position == 1 and d.divergence.changed_parts == ["extra"]


def test_jump_to_snapshot_position(db, blobs, journal):
    record_run(db, blobs, journal, ["a", "a", "b"])
    eng = ReplayEngine(journal, run_id=2, start_position=2)
    d = eng.next("estimator", key("b"))
    assert d.action == "replay" and d.position == 2
    eng2 = ReplayEngine(journal, run_id=2, start_position=1)
    assert eng2.next("estimator", key("a")).occurrence == 1
    assert eng.has_recorded_after(2) and not eng.has_recorded_after(3)
