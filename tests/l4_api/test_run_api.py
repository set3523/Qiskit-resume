import numpy as np
import pytest
from qiskit.circuit import Parameter, QuantumCircuit
from qiskit.primitives import StatevectorEstimator, StatevectorSampler
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler import generate_preset_pass_manager

import qiskit_resume as resume
from qiskit_resume.l4_api.run import core as run_core

from .helpers import FakeIBMEstimator, FakeService


def circ():
    t = Parameter("t")
    qc = QuantumCircuit(1)
    qc.ry(t, 0)
    return qc


H = SparsePauliOp("Z")


def loop(path, angles, est=None, **kw):
    est = est or resume.wrap(StatevectorEstimator())
    out = []
    with resume.resume_run(path, quiet=True, **kw) as run:
        for a in angles:
            out.append(float(est.run([(circ(), H, [a])]).result()[0].data.evs))
    return out, run


def test_replay_and_seed_is_fixed(tmp_path):
    p = tmp_path / "e.qresume"
    a, r1 = loop(p, [0.1, 0.2], seed=7)
    b, r2 = loop(p, [0.1, 0.2, 0.3])
    assert a == b[:2] and r2.stats == {"replay": 2, "reattach": 0, "execute": 1} and r2.seed == 7
    with pytest.raises(resume.ImmutableSettingError):
        loop(p, [0.1], seed=8)


def test_seed_and_rng_outside_run_raise():
    with pytest.raises(resume.NoActiveRunError):
        resume.rng()
    with pytest.raises(resume.NoActiveRunError):
        resume.wrap(StatevectorEstimator()).run([(circ(), H, [0.1])])


def test_divergence_unchanged_code_warns_and_branches(tmp_path, capsys):
    p = tmp_path / "e.qresume"
    loop(p, [0.1, 0.2, 0.3])
    _, r = loop(p, [0.1, 0.25, 0.3])
    err = capsys.readouterr().err
    assert "unseeded random" in err and r.stats == {"replay": 1, "reattach": 0, "execute": 2}
    _, r3 = loop(p, [0.1, 0.25, 0.3])  # new branch is now the record
    assert r3.stats["replay"] == 3


def test_divergence_stop_option(tmp_path):
    p = tmp_path / "e.qresume"
    loop(p, [0.1, 0.2])
    with pytest.raises(resume.DivergenceStop):
        loop(p, [0.1, 0.9], on_divergence="stop")


def test_code_change_is_treated_as_intentional(tmp_path, monkeypatch, capsys):
    p = tmp_path / "e.qresume"
    monkeypatch.setattr(run_core, "_code_fingerprint", lambda: "v1")
    loop(p, [0.1, 0.2])
    monkeypatch.setattr(run_core, "_code_fingerprint", lambda: "v2")
    loop(p, [0.1, 0.9])
    assert "script (or its arguments) changed" in capsys.readouterr().err


def test_snapshot_resume_and_missing_state_detection(tmp_path, capsys):
    p = tmp_path / "e.qresume"
    est = resume.wrap(StatevectorEstimator())

    def program(save_velocity, n=6):
        with resume.resume_run(p, quiet=True) as run:
            snap = run.load_snapshot()
            i, theta, vel = (snap["i"], snap["theta"], snap.get("vel", 0.0)) if snap else (0, 0.0, 0.0)
            for i in range(i, n):
                e = float(est.run([(circ(), H, [theta])]).result()[0].data.evs)
                vel = 0.9 * vel + e
                theta = theta - 0.1 * vel
                state = {"i": i + 1, "theta": theta}
                if save_velocity:
                    state["vel"] = vel
                if i == 2:
                    run.snapshot(state)
            return theta, run

    ref, _ = program(True)
    t2, r2 = program(True)            # full state saved: resumes at the snapshot, replays the rest
    assert t2 == ref and r2.stats == {"replay": 3, "reattach": 0, "execute": 0}

    q = tmp_path / "f.qresume"
    p = q
    program(False)                    # velocity missing from the snapshot
    with pytest.raises(resume.DivergenceStop):
        program(False)
    assert "snapshot is missing some state" in capsys.readouterr().err
    t4, r4 = program(False)           # snapshot invalidated -> full replay from the start, no QPU
    assert r4.stats["execute"] == 0 and r4.stats["replay"] == 6


def test_function_wrap_and_transpile_cache(tmp_path):
    p = tmp_path / "e.qresume"
    calls = {"n": 0}

    @resume.wrap
    def heavy(n, scale=1.0):
        calls["n"] += 1
        return {"h": np.arange(n) * scale}

    pm = resume.wrap(generate_preset_pass_manager(optimization_level=1, basis_gates=["rz", "sx", "cx"]),
                     backend="local")
    for _ in range(2):
        with resume.resume_run(p, quiet=True) as run:
            v = heavy(3, scale=2.0)
            isa = pm.run(circ())
            est = resume.wrap(StatevectorEstimator())
            e = float(est.run([(isa, H, [0.4])]).result()[0].data.evs)
    assert calls["n"] == 1 and list(v["h"]) == [0, 2, 4]
    assert run.stats == {"replay": 3, "reattach": 0, "execute": 0}
    assert abs(e - np.cos(0.4)) < 1e-9


def test_sampler_replay(tmp_path):
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    p = tmp_path / "e.qresume"
    outs = []
    for _ in range(2):
        with resume.resume_run(p, quiet=True):
            s = resume.wrap(StatevectorSampler(seed=resume.seed))
            outs.append(s.run([qc], shots=50).result()[0].data.meas.get_bitstrings())
    assert outs[0] == outs[1]


def test_ibm_like_reattach_retry_quota_budget(tmp_path):
    svc = FakeService()
    p = tmp_path / "e.qresume"
    est = resume.wrap(FakeIBMEstimator(svc, plan=["backend is in maintenance", None]))
    with resume.resume_run(p, quiet=True) as run:
        job = est.run([(circ(), H, [0.1])])
        assert job.job_id().startswith("job-")
        job.result()                                        # device error -> retried once
        est.run([(circ(), H, [0.2])])                       # submitted, result never fetched (process "dies")
        j = run.journal.list_calls()
        assert j[0].usage_s == 2.0 and j[1].status == "submitted"
    assert est._inner.submitted == 3

    est2 = resume.wrap(FakeIBMEstimator(svc))
    with resume.resume_run(p, quiet=True) as run2:
        est2.run([(circ(), H, [0.1])]).result()
        est2.run([(circ(), H, [0.2])]).result()             # reattached to the existing job
    assert est2._inner.submitted == 0 and run2.stats == {"replay": 1, "reattach": 1, "execute": 0}

    est3 = resume.wrap(FakeIBMEstimator(svc, plan=["Your instance has insufficient quota"]))
    with pytest.raises(resume.QuotaExhausted):
        with resume.resume_run(p, quiet=True):
            est3.run([(circ(), H, [0.3])]).result()

    est4 = resume.wrap(FakeIBMEstimator(svc))
    with pytest.raises(resume.BudgetExceeded):
        with resume.resume_run(p, quiet=True, qpu_budget_s=3.0):
            for a in (0.4, 0.5, 0.6):
                est4.run([(circ(), H, [a])]).result()
    assert est4._inner.submitted == 1   # 2s used; next estimate 2.4s (past usage x1.2) > 1s left -> stop


def test_backend_change_warn_and_stop(tmp_path, capsys):
    svc = FakeService()
    p = tmp_path / "e.qresume"
    with resume.resume_run(p, quiet=True):
        resume.wrap(FakeIBMEstimator(svc, name="dev_a")).run([(circ(), H, [0.1])]).result()
    with resume.resume_run(p, quiet=True):
        resume.wrap(FakeIBMEstimator(svc, name="dev_b")).run([(circ(), H, [0.2])]).result()
    assert "device changed" in capsys.readouterr().err
    with pytest.raises(resume.BackendChangedStop):
        with resume.resume_run(p, quiet=True, on_backend_change="stop"):
            resume.wrap(FakeIBMEstimator(svc, name="dev_c")).run([(circ(), H, [0.3])])


def test_already_running(tmp_path):
    p = tmp_path / "e.qresume"
    with resume.resume_run(p, quiet=True):
        with pytest.raises(resume.AlreadyRunningError):
            with resume.resume_run(p, quiet=True):
                pass


def test_queued_job_timeout_keeps_job_and_cancelled_job_is_resubmitted(tmp_path):
    from .helpers import FakeJob

    svc = FakeService()
    p = tmp_path / "e.qresume"

    class Queued(FakeIBMEstimator):
        def run(self, pubs, *, precision=None):
            self.submitted += 1
            return FakeJob(self._service, self._local.run(pubs), state="QUEUED")

    est = resume.wrap(Queued(svc))
    with pytest.raises(resume.JobWaitTimeout):
        with resume.resume_run(p, quiet=True, job_wait_timeout_s=1):
            est.run([(circ(), H, [0.1])]).result()
    job_id = next(iter(svc.jobs))
    svc.jobs[job_id].state = "CANCELLED"

    est2 = resume.wrap(FakeIBMEstimator(svc))
    with resume.resume_run(p, quiet=True) as run:
        est2.run([(circ(), H, [0.1])]).result()
        calls = run.journal.list_calls(include_invalid=True)
    assert est2._inner.submitted == 1 and [c.status for c in calls] == ["invalid", "done"]


def test_rng_named_streams_and_seed_for(tmp_path):
    """resume.rng("name") does not depend on call order; seed_for gives distinct stable ints."""
    import numpy as np

    import qiskit_resume as resume

    f = tmp_path / "rng.qresume"
    with resume.resume_run(f, seed=11):
        a1 = resume.rng("init").random(3)
        resume.rng()  # an extra unnamed call must not shift named streams
        b1 = resume.rng("noise").random(3)
        s1 = (resume.seed_for("transpile"), resume.seed_for("sampler"))
        e1 = resume.rng(seed=5).random()
    with resume.resume_run(f):
        b2 = resume.rng("noise").random(3)  # order swapped on purpose
        a2 = resume.rng("init").random(3)
        s2 = (resume.seed_for("transpile"), resume.seed_for("sampler"))
    assert np.array_equal(a1, a2) and np.array_equal(b1, b2) and not np.array_equal(a1, b1)
    assert s1 == s2 and s1[0] != s1[1]
    assert e1 == np.random.default_rng(5).random()


def test_pass_manager_helper_is_recorded_and_seeded(tmp_path):
    """resume.pass_manager builds lazily with a fixed seed_transpiler and replays the transpile."""
    from qiskit.circuit.library import real_amplitudes

    import qiskit_resume as resume

    f = tmp_path / "pm.qresume"
    pm = resume.pass_manager(optimization_level=1, basis_gates=["rz", "sx", "x", "cx"])  # before resume_run
    outs = []
    for _ in range(2):
        with resume.resume_run(f, seed=3) as run:
            outs.append(pm.run(real_amplitudes(3, reps=1)))
            stats = dict(run.stats)
    from qiskit_resume.l3_qiskit.circuits import circuit_canonical_bytes

    assert stats["replay"] == 1 and circuit_canonical_bytes(outs[0]) == circuit_canonical_bytes(outs[1])


def test_backend_helper_records_and_reuses_device(tmp_path):
    """resume.backend: least busy on the first run, the recorded device on reruns."""
    import qiskit_resume as resume

    class B:
        def __init__(self, name):
            self.name = name

    class Svc:
        def __init__(self, busy):
            self.busy = busy

        def least_busy(self, **kw):
            return B(self.busy)

        def backend(self, name):
            return B(name)

    f = tmp_path / "dev.qresume"
    with resume.resume_run(f):
        assert resume.backend(Svc("ibm_a")).name == "ibm_a"
    with resume.resume_run(f):
        assert resume.backend(Svc("ibm_b")).name == "ibm_a"  # least busy changed; the record wins
    with resume.resume_run(f, on_backend_change="stop"):
        try:
            resume.backend(Svc("ibm_b"), name="ibm_c")  # explicit switch is reported like any device change
        except resume.BackendChangedStop:
            pass
        else:
            raise AssertionError("expected BackendChangedStop")


def test_named_rng_in_a_loop_continues_the_stream(tmp_path):
    """rng("x").random() called repeatedly draws new numbers (same object), and replays identically."""
    import qiskit_resume as resume

    f = tmp_path / "loop.qresume"
    runs = []
    for _ in range(2):
        with resume.resume_run(f, seed=2):
            runs.append([resume.rng("jitter").random() for _ in range(4)])
    assert len(set(runs[0])) == 4 and runs[0] == runs[1]


def test_wrapped_function_stores_qiskit_objects(tmp_path):
    """A slow classical step returning a SparsePauliOp / circuit is recorded and skipped on rerun."""
    from qiskit import QuantumCircuit
    from qiskit.quantum_info import SparsePauliOp

    import qiskit_resume as resume

    calls = []

    @resume.wrap
    def build(n, ref):
        calls.append(n)
        qc = QuantumCircuit(n)
        qc.h(0)
        return {"H": SparsePauliOp.from_list([("Z" * n, 0.5), ("X" * n, -1.0)]), "circ": qc}

    f = tmp_path / "fn.qresume"
    ref = QuantumCircuit(2)
    out = []
    for _ in range(2):
        with resume.resume_run(f):
            out.append(build(2, ref))
    assert calls == [2]  # second run replayed, the function did not run
    assert out[0]["H"] == out[1]["H"] and isinstance(out[1]["circ"], QuantumCircuit)
    assert out[1]["circ"].count_ops() == {"h": 1}
