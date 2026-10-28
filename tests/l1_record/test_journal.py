import pytest

from qiskit_resume.l1_record.hashing import digest_parts


def key(s):
    return digest_parts({"q": s.encode()})


def test_lifecycle_and_find(db, blobs, journal):
    k = key("a")
    rec = journal.record_submitted(run_id=1, seq=0, kind="estimator", key=k, occurrence=0, job_id="j1")
    assert rec.status == "submitted" and rec.parts == k.parts
    assert journal.find("estimator", k.key_hash, 0).id == rec.id
    with db.tx():
        ref = blobs.put(b"result", kind="result")
    done = journal.record_done(rec.id, result_ref=ref, usage_s=1.5)
    assert done.status == "done" and done.usage_s == 1.5
    with pytest.raises(ValueError):
        journal.record_failed(rec.id, error="x")  # done never changes


def test_result_blob_must_exist(journal):
    rec = journal.record_submitted(run_id=1, seq=0, kind="sampler", key=key("a"), occurrence=0)
    with pytest.raises(KeyError):
        journal.record_done(rec.id, result_ref="0" * 64)


def test_invalidate_keeps_history_and_frees_key(journal):
    k = key("a")
    r1 = journal.record_submitted(run_id=1, seq=0, kind="estimator", key=k, occurrence=0)
    journal.record_submitted(run_id=1, seq=1, kind="estimator", key=key("b"), occurrence=0)
    assert journal.invalidate_from(1) == [r1.id + 1]
    assert journal.max_seq() == 0
    journal.invalidate([r1.id])
    assert journal.find("estimator", k.key_hash, 0) is None
    r2 = journal.record_submitted(run_id=2, seq=0, kind="estimator", key=k, occurrence=0)  # allowed again
    assert r2.id != r1.id
    assert len(journal.list_calls(include_invalid=True)) == 3
    assert [r.id for r in journal.list_calls()] == [r2.id]
