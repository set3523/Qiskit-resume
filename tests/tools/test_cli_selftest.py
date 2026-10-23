import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

SCRIPT = textwrap.dedent('''
    import sys
    import numpy as np
    try:
        from qiskit.circuit.library import real_amplitudes
    except ImportError:
        from qiskit.circuit.library import RealAmplitudes as real_amplitudes
    from qiskit.primitives import StatevectorEstimator
    from qiskit.quantum_info import SparsePauliOp
    import qiskit_resume as resume

    BAD = "--bad" in sys.argv
    SHIFT = float(sys.argv[sys.argv.index("--shift") + 1]) if "--shift" in sys.argv else 0.0
    ansatz = real_amplitudes(3, reps=1)
    ham = SparsePauliOp.from_list([("ZZI", 1.0), ("IXX", 0.5)])
    est = resume.wrap(StatevectorEstimator())
    with resume.resume_run(sys.argv[1], quiet=True) as run:
        gen = np.random.default_rng() if BAD else resume.rng()
        theta = gen.uniform(-1, 1, ansatz.num_parameters)
        snap = run.load_snapshot()
        start = 0
        if snap:
            start, theta = snap["i"], snap["theta"]
        for i in range(start, 12):
            e = float(est.run([(ansatz, ham, theta + SHIFT * (i == 10))]).result()[0].data.evs)
            theta = theta - 0.1 * e
            if i % 4 == 3:
                run.snapshot({"i": i + 1, "theta": theta})
''')


def cli(*args, check=False):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    p = subprocess.run([sys.executable, "-m", "qiskit_resume", *map(str, args)], env=env, capture_output=True, text=True)
    if check:
        assert p.returncode == 0, p.stderr + p.stdout
    return p


def script(tmp_path):
    s = tmp_path / "exp_script.py"
    s.write_text(SCRIPT)
    return s


def test_selftest_good_and_bad(tmp_path):
    s = script(tmp_path)
    good = cli("selftest", s, "--json", "--", str(tmp_path / "ignored.qresume"))
    rep = json.loads(good.stdout)
    assert good.returncode == 0 and rep["ok"] and rep["reference_calls"] == 12 and len(rep["points"]) == 3
    bad = cli("selftest", s, "--", str(tmp_path / "ignored.qresume"), "--bad")
    assert bad.returncode == 1 and "NOT resumable" in bad.stdout


def test_check_status_export_pack_fork_compact(tmp_path):
    s = script(tmp_path)
    exp = tmp_path / "e.qresume"
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    assert subprocess.run([sys.executable, str(s), str(exp)], env=env).returncode == 0

    r = cli("check", s, "--file", exp, "--json", "--", str(exp))
    rep = json.loads(r.stdout)
    assert r.returncode == 0 and rep["next_action"] == "none"

    r = cli("check", s, "--file", exp, "--json", "--", str(exp), "--shift", "0.5")
    rep = json.loads(r.stdout)
    assert r.returncode == 1 and rep["divergence"]["position"] == 10
    assert rep["divergence"]["changed_parts"] == ["pub0.params"]

    st = json.loads(cli("status", exp, "--json", check=True).stdout)
    assert st["calls_by_kind"] == {"estimator": 12} and st["snapshots"] == 3 and st["running"] is None

    out = tmp_path / "x.csv"
    cli("export", exp, "--format", "csv", "--group-by", "run", "-o", out, check=True)
    assert out.read_text().splitlines()[0].startswith("group,id,run_id")
    j = json.loads(cli("export", exp, check=True).stdout)
    assert len(j) == 12 and isinstance(j[0]["result"][0]["evs"], float)

    cli("copy", exp, tmp_path / "c.qresume", check=True)
    packed = cli("pack", tmp_path / "c.qresume", check=True).stdout.split()[1]
    os.remove(tmp_path / "c.qresume")
    cli("unpack", packed, check=True)

    fork = tmp_path / "f.qresume"
    snaps = st["snapshots"]
    cli("fork", exp, "--at-snapshot", 2, "--to", fork, check=True)
    p = subprocess.run([sys.executable, str(s), str(fork), "--shift", "0.5"], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr                       # branch continues without stopping

    cli("compact", exp, "--keep-snapshots", 1, "--drop-invalid", check=True)
    st2 = json.loads(cli("status", exp, "--json", check=True).stdout)
    assert st2["snapshots"] == 1 and snaps == 3
