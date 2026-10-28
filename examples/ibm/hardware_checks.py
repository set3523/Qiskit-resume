"""Hardware checks for qiskit-resume on a real IBM device (checks 5-9 of the test plan).

Each check runs small child processes (1-2 jobs each, about 6-7 jobs in total; each job costs
roughly 10-15 QPU seconds on Open Plan) and prints PASS / FAIL / SKIP with the evidence.

    $env:QISKIT_IBM_DEVICE = "ibm_xxx"
    python hardware_checks.py                    # all checks; records go to .\\hwchecks\\
    python hardware_checks.py --only 5,6         # a subset

    5  batch      one Estimator job with 2 pubs (one a broadcast sweep) and one Sampler job with
                  2 circuits (one a 3-point sweep); a second run must replay both bit-for-bit
    6  pending    hard-kill right after the job is submitted (usually still QUEUED); the rerun must
                  reconnect to that same job id instead of submitting a new one
    7  timeout    job_wait_timeout_s=2 stops the run cleanly while the job waits; the rerun must
                  reconnect to it
    8  device     rerun the record of check 6 on a different device with on_backend_change="stop";
                  it must stop before submitting anything (no QPU time)
    9  newprims   the new client-side Estimator/Sampler of qiskit-ibm-runtime >= 0.50 (which replace
                  EstimatorV2/SamplerV2): kill right after the Estimator job is submitted, rerun must
                  reconnect and run the Sampler job, a third run must replay both identically

QISKIT_IBM_DEVICE="fake" runs 5, 8 and 9 (replay part) on local fake devices (no account, needs
qiskit-aer for 9); 6 and 7 need real IBM jobs and are skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

EXIT_TIMEOUT = 4
EXIT_DEVICE_STOP = 5
EXIT_BUDGET = 6
QPU_BUDGET_S = 40.0  # per child run: a safety net, far above what each check needs


def say(text: str) -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)


# ---------------------------------------------------------------------------
# child side: the actual Qiskit work, protected by qiskit-resume
# ---------------------------------------------------------------------------


def get_backend(name: str):
    if name == "fake":
        from qiskit_ibm_runtime.fake_provider import FakeManilaV2

        return FakeManilaV2()
    if name == "fake2":
        from qiskit_ibm_runtime.fake_provider import FakeLimaV2

        return FakeLimaV2()
    from qiskit_ibm_runtime import QiskitRuntimeService

    service = QiskitRuntimeService()
    return service.backend(name) if name else service.least_busy(operational=True, simulator=False)


def child(check: str, file: str, device: str, timeout: float | None, on_backend_change: str) -> int:
    import warnings

    warnings.filterwarnings("ignore", category=DeprecationWarning)  # EstimatorV2/SamplerV2 deprecation notices
    import numpy as np
    from qiskit.circuit import QuantumCircuit
    from qiskit.quantum_info import SparsePauliOp
    from qiskit.transpiler import generate_preset_pass_manager
    from qiskit_ibm_runtime import EstimatorV2, SamplerV2

    try:
        from qiskit.circuit.library import real_amplitudes
    except ImportError:
        from qiskit.circuit.library import RealAmplitudes as real_amplitudes

    import qiskit_resume as resume

    backend = get_backend(device)
    print(f"device: {backend.name}", flush=True)
    try:
        with resume.resume_run(file, log_calls=True, qpu_budget_s=QPU_BUDGET_S, job_wait_timeout_s=timeout,
                               on_backend_change=on_backend_change):
            pm = resume.wrap(generate_preset_pass_manager(backend=backend, optimization_level=1, seed_transpiler=7),
                             backend=backend)
            est = resume.wrap(EstimatorV2(mode=backend, options={"default_shots": 1000}))
            ansatz = real_amplitudes(2, reps=1)
            isa = pm.run(ansatz)
            zz = SparsePauliOp("ZZ").apply_layout(isa.layout)
            xi = SparsePauliOp("XI").apply_layout(isa.layout)
            theta = np.array([0.1, 0.2, 0.3, 0.4])
            out: dict = {}
            if check == "batch":
                sweep = np.array([[0.1, 0.2, 0.3, 0.4], [1.0, 0.5, 0.0, 2.0], [2.0, 1.0, 0.5, 0.1]])
                r = est.run([(isa, [[zz], [xi]], sweep), (isa, zz, theta)]).result()  # pub0 evs shape (2, 3)
                out["est_pub0_evs"] = np.asarray(r[0].data.evs).tolist()
                out["est_pub0_stds"] = np.asarray(r[0].data.stds).tolist()
                out["est_pub1_evs"] = float(r[1].data.evs)

                bell = QuantumCircuit(2)
                bell.h(0)
                bell.cx(0, 1)
                bell.measure_all()
                meas = ansatz.copy()
                meas.measure_all()
                isa_bell, isa_meas = pm.run([bell, meas])
                smp = resume.wrap(SamplerV2(mode=backend, options={"default_shots": 500}))
                r = smp.run([isa_bell, (isa_meas, sweep)]).result()
                out["smp_pub0_counts"] = dict(sorted(r[0].data.meas.get_counts().items()))
                out["smp_pub1_counts"] = [dict(sorted(r[1].data.meas.get_counts(loc=i).items())) for i in range(3)]
            elif check == "newprims":
                from qiskit_ibm_runtime.executor_estimator import Estimator
                from qiskit_ibm_runtime.executor_sampler import Sampler

                est2 = resume.wrap(Estimator(mode=backend, options={"default_shots": 1000}))
                out["evs"] = float(est2.run([(isa, zz, theta)]).result()[0].data.evs)
                meas = ansatz.copy()
                meas.measure_all()
                smp2 = resume.wrap(Sampler(mode=backend, options={"default_shots": 1000}))
                r = smp2.run([(pm.run(meas), theta)]).result()
                out["counts"] = dict(sorted(r[0].data.meas.get_counts().items()))
            else:  # pending / timeout / device: a single Estimator job
                out["evs"] = float(est.run([(isa, zz, theta)]).result()[0].data.evs)
    except resume.JobWaitTimeout as exc:
        print(f"TIMEOUT {exc}", flush=True)
        return EXIT_TIMEOUT
    except resume.BackendChangedStop as exc:
        print(f"DEVICE-STOP {exc}", flush=True)
        return EXIT_DEVICE_STOP
    except resume.BudgetExceeded as exc:
        print(f"BUDGET {exc}", flush=True)
        return EXIT_BUDGET
    print("RESULT " + json.dumps(out, sort_keys=True), flush=True)
    return 0


# ---------------------------------------------------------------------------
# parent side: start children, kill them, read their logs, judge
# ---------------------------------------------------------------------------


class Run:
    def __init__(self, label: str, code: int | None, lines: list[str]) -> None:
        self.label, self.code, self.lines = label, code, lines

    def has(self, pattern: str) -> re.Match | None:
        for line in self.lines:
            m = re.search(pattern, line)
            if m:
                return m
        return None

    @property
    def result(self) -> dict | None:
        for line in self.lines:
            if line.startswith("RESULT "):
                return json.loads(line[7:])
        return None


def run_child(label: str, check: str, file: Path, device: str, *, timeout: float | None = None,
              on_backend_change: str = "warn", kill_on: str | None = None, kill_delay: float = 0.0) -> Run:
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), "--child", check, "--file", str(file),
           "--device", device, "--on-backend-change", on_backend_change]
    if timeout is not None:
        cmd += ["--timeout", str(timeout)]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    say(f"\n--- {label} ---")
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)
    lines: list[str] = []
    for raw in proc.stdout:
        line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
        lines.append(line)
        if not line.startswith("RESULT "):
            say(f"  [{label}] {line}")
        if kill_on and re.search(kill_on, line):
            time.sleep(kill_delay)
            proc.kill()
            say(f"  [{label}] >>> KILLED (hard kill) right after: {line.strip()}")
            break
    return Run(label, proc.wait(), lines)


def fresh(path: Path) -> Path:
    for p in (path, path.with_name(path.name + ".lock")):
        if p.exists():
            p.unlink()
    return path


def check_batch(work: Path, device: str) -> tuple[str, str]:
    f = fresh(work / "c5_batch.qresume")
    a = run_child("5a first run", "batch", f, device)
    if a.code != 0 or a.result is None:
        return "FAIL", f"first run failed (exit {a.code})"
    b = run_child("5b rerun", "batch", f, device)
    if b.code != 0 or b.result is None:
        return "FAIL", f"rerun failed (exit {b.code})"
    if not b.has(r"executed 0 calls"):
        return "FAIL", "rerun executed calls instead of replaying"
    if a.result != b.result:
        return "FAIL", "replayed results differ from the original"
    return "PASS", "2 jobs (Estimator 2 pubs incl. broadcast sweep; Sampler 2 circuits incl. 3-point sweep) replayed identically"


def check_pending(work: Path, device: str) -> tuple[str, str]:
    if device.startswith("fake"):
        return "SKIP", "needs real IBM jobs (fake devices have no job ids)"
    f = fresh(work / "c6_pending.qresume")
    a = run_child("6a run, killed right after submit", "pending", f, device,
                  kill_on=r"submitted as job \S+", kill_delay=0.5)
    m = a.has(r"submitted as job (\S+)")
    if not m:
        return "FAIL", "the job was never submitted"
    job_id = m.group(1)
    b = run_child("6b rerun", "pending", f, device)
    r = b.has(r"reconnected to job (\S+) \((\w+)\)")
    if b.code != 0 or not r:
        return "FAIL", f"rerun did not reconnect (exit {b.code})"
    if r.group(1) != job_id:
        return "FAIL", f"reconnected to {r.group(1)}, expected {job_id}"
    if b.has(r"submitted as job"):
        return "FAIL", "rerun submitted a new job"
    return "PASS", f"reconnected to the same job {job_id} (state at reconnect: {r.group(2)}); nothing resubmitted"


def check_timeout(work: Path, device: str) -> tuple[str, str]:
    if device.startswith("fake"):
        return "SKIP", "needs real IBM jobs (fake devices finish instantly)"
    f = fresh(work / "c7_timeout.qresume")
    a = run_child("7a run with job_wait_timeout_s=2", "timeout", f, device, timeout=2.0)
    if a.code == 0:
        return "SKIP", "the job finished within 2 s, so the timeout never triggered (queue was empty)"
    if a.code != EXIT_TIMEOUT:
        return "FAIL", f"expected a clean timeout stop, got exit {a.code}"
    m = a.has(r"submitted as job (\S+)")
    b = run_child("7b rerun without timeout", "timeout", f, device)
    r = b.has(r"reconnected to job (\S+)")
    if b.code != 0 or not r or (m and r.group(1) != m.group(1)) or b.has(r"submitted as job"):
        return "FAIL", f"rerun did not reconnect to the waiting job (exit {b.code})"
    return "PASS", f"clean stop after 2 s of waiting, rerun reconnected to job {r.group(1)}"


def check_device(work: Path, device: str, other: str | None) -> tuple[str, str]:
    src = work / ("c5_batch.qresume" if device.startswith("fake") else "c6_pending.qresume")
    if not src.exists():
        return "SKIP", f"needs the record of an earlier check ({src.name})"
    if other is None:
        if device.startswith("fake"):
            other = "fake2"
        else:
            from qiskit_ibm_runtime import QiskitRuntimeService

            names = [b.name for b in QiskitRuntimeService().backends(operational=True, simulator=False)]
            others = [n for n in names if n != device]
            if not others:
                return "SKIP", "no second device available to this account"
            other = others[0]
    f = fresh(work / "c8_device.qresume")
    shutil.copyfile(src, f)
    check = "batch" if device.startswith("fake") else "pending"
    a = run_child(f"8 rerun on {other} with on_backend_change='stop'", check, f, other, on_backend_change="stop")
    if a.code != EXIT_DEVICE_STOP:
        return "FAIL", f"expected a device-change stop, got exit {a.code}"
    if a.has(r"submitted as job"):
        return "FAIL", "a job was submitted on the new device"
    return "PASS", f"device change {device} -> {other} detected; stopped before submitting anything"


def check_newprims(work: Path, device: str) -> tuple[str, str]:
    try:
        import qiskit_ibm_runtime.executor_estimator  # noqa: F401
    except ImportError:
        return "SKIP", "qiskit-ibm-runtime < 0.50 (no client-side Estimator/Sampler yet)"
    f = fresh(work / "c9_newprims.qresume")
    real = not device.startswith("fake")
    a = run_child("9a run, killed right after the Estimator job is submitted" if real else "9a first run",
                  "newprims", f, device, kill_on=r"submitted as job \S+" if real else None, kill_delay=0.5)
    detail = ""
    if real:
        m = a.has(r"submitted as job (\S+)")
        if not m:
            return "FAIL", "the Estimator job was never submitted"
        b = run_child("9b rerun", "newprims", f, device)
        r = b.has(r"reconnected to job (\S+) \((\w+)\)")
        if b.code != 0 or not r or r.group(1) != m.group(1):
            return "FAIL", f"rerun did not reconnect to job {m.group(1)} (exit {b.code})"
        first = b
        detail = f"reconnected to {m.group(1)} ({r.group(2)}); "
    else:
        if a.code != 0:
            return "FAIL", f"first run failed (exit {a.code})"
        first = a
    c = run_child("9c replay", "newprims", f, device)
    if c.code != 0 or not c.has(r"executed 0 calls") or c.result != first.result:
        return "FAIL", f"replay was not identical (exit {c.code})"
    return "PASS", detail + "Estimator + Sampler results replayed identically"


def main() -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description="qiskit-resume hardware checks (5-9)")
    ap.add_argument("--only", help="comma-separated check numbers, e.g. 5,6")
    ap.add_argument("--device", default=os.environ.get("QISKIT_IBM_DEVICE", ""), help="device (default: $QISKIT_IBM_DEVICE)")
    ap.add_argument("--other-device", help="device for check 8 (default: any other available device)")
    ap.add_argument("--workdir", default="hwchecks", help="where the check records are written")
    ap.add_argument("--child", help=argparse.SUPPRESS)
    ap.add_argument("--file", help=argparse.SUPPRESS)
    ap.add_argument("--timeout", type=float, help=argparse.SUPPRESS)
    ap.add_argument("--on-backend-change", default="warn", help=argparse.SUPPRESS)
    a = ap.parse_args()
    if a.child:
        return child(a.child, a.file, a.device, a.timeout, a.on_backend_change)
    if not a.device:
        raise SystemExit('set the device first, e.g.  $env:QISKIT_IBM_DEVICE = "ibm_kingston"  (or "fake")')
    work = Path(a.workdir)
    work.mkdir(exist_ok=True)
    wanted = {int(x) for x in a.only.split(",")} if a.only else {5, 6, 7, 8, 9}
    checks = [(5, "batch: multi-pub / sweep jobs replay identically", lambda: check_batch(work, a.device)),
              (6, "pending: kill right after submit -> rerun reconnects", lambda: check_pending(work, a.device)),
              (7, "timeout: clean stop while waiting -> rerun reconnects", lambda: check_timeout(work, a.device)),
              (8, "device: change detected before any submit", lambda: check_device(work, a.device, a.other_device)),
              (9, "newprims: client-side Estimator/Sampler (runtime >= 0.50)", lambda: check_newprims(work, a.device))]
    results = []
    for n, title, fn in checks:
        if n not in wanted:
            continue
        say(f"\n=== check {n}: {title} ===")
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001
            status, detail = "FAIL", f"{type(exc).__name__}: {exc}"
        results.append((n, status, detail))
        say(f">>> check {n}: {status} - {detail}")
    say("\n=== summary ===")
    for n, status, detail in results:
        say(f"  {n}  {status:4}  {detail}")
    say(f"records: {work.resolve()}   (qresume status <file> shows the QPU seconds used)")
    return 0 if all(s != "FAIL" for _, s, _ in results) else 1


if __name__ == "__main__":
    sys.exit(main())
