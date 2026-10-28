"""Examples 01-04 run end to end, and a rerun replays everything (no execution)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EX = ROOT / "examples"


@pytest.mark.parametrize("name,extra", [
    ("01_estimator_loop.py", []),
    ("02_vqe_scipy.py", ["30"]),
    ("03_qaoa_sampler.py", []),
    ("04_snapshot_and_function.py", []),
])
def test_example_runs_and_replays(tmp_path, name, extra):
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    f = tmp_path / "e.qresume"
    for i in range(2):
        p = subprocess.run([sys.executable, str(EX / name), str(f), *extra], env=env, capture_output=True,
                           text=True, timeout=600)
        assert p.returncode == 0, p.stderr
    assert "executed 0 calls" in p.stderr


@pytest.mark.parametrize("name,sets,kill", [
    ("vqe_ising_spsa.py", ["num_qubits=6", "maxiter=40"], 30),
    ("qaoa_maxcut.py", ["num_nodes=8", "reps=2"], 12),
    ("vqd_excited_states.py", ["num_qubits=4", "k=2", "maxiter=60"], 100),
])
def test_crash_and_resume_runner(tmp_path, name, sets, kill):
    """Plain Qiskit workloads: killed by the runner, resumed, and equal to an uninterrupted run."""
    pytest.importorskip("qiskit_algorithms")
    env = dict(os.environ, PYTHONPATH=str(ROOT))
    cmd = [sys.executable, str(EX / "crash_and_resume.py"), str(EX / "standard" / name), "--kill-at-call", str(kill),
           "--compare"]
    for s in sets:
        cmd += ["--set", s]
    p = subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True, text=True, timeout=900)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "KILLED after call" in p.stdout
    assert "SAME RESULT" in p.stdout
