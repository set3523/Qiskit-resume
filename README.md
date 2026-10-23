# qiskit-resume

**Run any Qiskit Primitives workflow so that it can be interrupted and resumed — without paying for the same QPU time twice.**

> **Status: alpha (0.2.0a1).** Fully tested on local simulators (Qiskit 1.4 and 2.x, Linux and Windows).
> The IBM path (job reconnection, QPU usage tracking) is tested against a fake service only so far;
> a real-hardware check is the next milestone. The file format may still change before 0.2.0.

Wrap your Estimator / Sampler, run your script inside `resume_run(...)`, and if it stops
(quota used up, laptop closed, network, Ctrl+C), just run the same command again:

- finished calls are **replayed instantly** from a local `.qresume` file,
- a job that was still running on IBM is **reconnected**, not resubmitted,
- only new work uses the QPU.

It works for any algorithm (VQE, QAOA, sampling sweeps, your own loops) because it records
*calls*, not algorithm state: on a rerun your Python code runs again from the top, gets the
recorded answers, rebuilds its own state (optimizer memory included) and continues at the
first unrecorded call. Built first for the IBM Quantum Open Plan (10 QPU minutes per rolling
28 days), where every second counts.

```python
from qiskit.primitives import StatevectorEstimator      # or qiskit_ibm_runtime.EstimatorV2(mode=backend)
import qiskit_resume as resume

estimator = resume.wrap(StatevectorEstimator())

with resume.resume_run("vqe.qresume"):
    theta0 = resume.rng().uniform(-3.14, 3.14, ansatz.num_parameters)   # randomness that replays
    res = minimize(lambda x: float(estimator.run([(ansatz, H, x)]).result()[0].data.evs),
                   theta0, method="COBYLA")
```

## Install

```bash
pip install qiskit-resume            # Qiskit >= 1.4 (tested 1.4 and 2.6), Python >= 3.10
pip install "qiskit-resume[ibm]"     # + qiskit-ibm-runtime: job reconnection, QPU usage
```

## What you wrap

| `resume.wrap(...)` | recorded | on replay |
|---|---|---|
| `EstimatorV2` / `SamplerV2` (local or IBM) | question hash, job id (before waiting), result, QPU seconds | result from the file; running IBM job reconnected |
| a pass manager — `resume.wrap(pm, backend=backend)` | transpiled circuit per (source circuit, device) | same circuit again, even if calibration changed |
| a slow, side-effect-free function — `@resume.wrap` | return value per arguments | value from the file |

Library algorithms that take an estimator (e.g. `qiskit_algorithms.VQE`) work too: pass the wrapped one.

## Rules of thumb

- **Randomness:** use `resume.rng()` / `resume.seed` (or seeded generators). Global `random`/`numpy`
  and `qiskit_algorithms.algorithm_globals` are seeded automatically.
- **Changed code:** if a replayed call asks a different question, you get a warning and execution
  continues from there (`on_divergence="stop"` to stop instead). Earlier records are kept as history.
- **Snapshots (optional):** `run.snapshot({...})` / `run.load_snapshot()` skip replaying the start.
  Put *every* variable your loop needs in it; a missing one is detected and reported.
- **Budget:** `resume_run(..., qpu_budget_s=120)` stops cleanly before the job that would exceed it.
- **Device change** is detected, recorded and warned about (`on_backend_change="stop"` to stop).

## Before spending QPU time

```bash
qresume selftest my_script.py -- my.qresume      # crash/resume at several points with synthetic results (no QPU)
qresume check my_script.py --file my.qresume -- my.qresume   # can this file continue? (no IBM contact)
qresume status my.qresume                        # calls, snapshots, device, QPU used, warnings
```

Other commands: `copy` (consistent copy while running), `export`, `quota`, `fork` (branch at a
snapshot), `pack`/`unpack`, `compact`.

## See it work

[`examples/crash_and_resume.py`](https://github.com/set3523/Qiskit-resume/blob/master/examples/crash_and_resume.py) takes a plain Qiskit workload
(VQE, QAOA, VQD from `qiskit_algorithms`, unmodified), wraps it, **kills the process part way**, runs it
again and checks the result equals an uninterrupted run, logging every call to the console:

```bash
git clone https://github.com/set3523/Qiskit-resume && cd Qiskit-resume/examples
pip install "qiskit-resume[examples]" scipy
python crash_and_resume.py standard/qaoa_maxcut.py --kill-at-call 100 --compare
```

## Limits

- A single circuit execution cannot be paused (quantum states can't be saved); interruption
  granularity is one primitive call.
- Code that is not deterministic in ways qiskit-resume can't seed (time, external data) may
  diverge on replay — you'll be told where.
- Calls not made through Primitives (`backend.run`, direct `Statevector` maths) are not recorded.

## Documentation

[DECISIONS](https://github.com/set3523/Qiskit-resume/blob/master/docs/DECISIONS.md) (why) ·
[ARCHITECTURE](https://github.com/set3523/Qiskit-resume/blob/master/docs/ARCHITECTURE.md) (layers and flow) ·
[CONTRACTS](https://github.com/set3523/Qiskit-resume/blob/master/docs/CONTRACTS.md) (every module's inputs/outputs) ·
[Korean README](https://github.com/set3523/Qiskit-resume/blob/master/README_ko.md)

License: MIT
