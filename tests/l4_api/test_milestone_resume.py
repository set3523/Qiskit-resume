"""Milestone 1: a local VQE killed mid-run and re-run gives exactly the uninterrupted result."""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

VQE = textwrap.dedent('''
    import json, os, sys
    import numpy as np
    from scipy.optimize import minimize
    try:
        from qiskit.circuit.library import real_amplitudes
    except ImportError:
        from qiskit.circuit.library import RealAmplitudes as real_amplitudes
    from qiskit.primitives import StatevectorEstimator
    from qiskit.quantum_info import SparsePauliOp
    import qiskit_resume as resume

    path, out = sys.argv[1], sys.argv[2]
    kill_after = int(os.environ.get("KILL_AFTER", "0"))
    ansatz = real_amplitudes(2, reps=2)
    ham = SparsePauliOp.from_list([("XX", 0.5), ("YY", 0.5), ("ZZ", 0.5), ("ZI", 0.3)])
    estimator = resume.wrap(StatevectorEstimator())
    calls = 0

    with resume.resume_run(path, quiet=True) as run:
        theta0 = resume.rng().uniform(-np.pi, np.pi, ansatz.num_parameters)   # random, but reproduced

        def cost(x):
            global calls
            calls += 1
            if kill_after and calls > kill_after:
                os._exit(9)                                             # hard crash, no cleanup
            return float(estimator.run([(ansatz, ham, x)]).result()[0].data.evs)

        res = minimize(cost, theta0, method="COBYLA", options={"maxiter": 60})
        json.dump({"fun": res.fun, "x": list(res.x), "stats": run.stats}, open(out, "w"))
''')


def run_script(tmp_path, exp, out, env_extra=None):
    script = tmp_path / "vqe.py"
    script.write_text(VQE)
    env = dict(os.environ, PYTHONPATH=str(ROOT), **(env_extra or {}))
    return subprocess.run([sys.executable, str(script), str(exp), str(out)], env=env, capture_output=True, text=True)


def test_killed_vqe_resumes_to_identical_result(tmp_path):
    ref = run_script(tmp_path, tmp_path / "ref.qresume", tmp_path / "ref.json")
    assert ref.returncode == 0, ref.stderr
    reference = json.loads((tmp_path / "ref.json").read_text())
    # the reference run gets a random seed; the interrupted experiment must use the same one to compare
    import sqlite3
    seed = json.loads(sqlite3.connect(tmp_path / "ref.qresume").execute(
        "SELECT value FROM meta WHERE key='seed'").fetchone()[0])

    exp = tmp_path / "exp.qresume"
    # pre-create the experiment with the same seed
    pre = subprocess.run([sys.executable, "-c",
                          f"import qiskit_resume as r\nwith r.resume_run({str(exp)!r}, seed={seed}, quiet=True): pass"],
                         env=dict(os.environ, PYTHONPATH=str(ROOT)), capture_output=True, text=True)
    assert pre.returncode == 0, pre.stderr

    crashed = run_script(tmp_path, exp, tmp_path / "x.json", {"KILL_AFTER": "25"})
    assert crashed.returncode == 9

    resumed = run_script(tmp_path, exp, tmp_path / "exp.json")
    assert resumed.returncode == 0, resumed.stderr
    got = json.loads((tmp_path / "exp.json").read_text())
    assert got["fun"] == reference["fun"] and got["x"] == reference["x"]          # bit-identical
    assert got["stats"]["replay"] == 25 and got["stats"]["execute"] == reference["stats"]["execute"] - 25


def test_rerun_after_finish_replays_everything(tmp_path):
    exp = tmp_path / "exp.qresume"
    first = run_script(tmp_path, exp, tmp_path / "a.json")
    second = run_script(tmp_path, exp, tmp_path / "b.json")
    assert first.returncode == 0 and second.returncode == 0, second.stderr
    a, b = json.loads((tmp_path / "a.json").read_text()), json.loads((tmp_path / "b.json").read_text())
    assert a["fun"] == b["fun"] and b["stats"]["execute"] == 0 and b["stats"]["replay"] == a["stats"]["execute"]
