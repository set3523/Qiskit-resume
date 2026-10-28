# qiskit-resume

**Run any Qiskit Primitives workflow so that it can be interrupted and resumed — without paying for the same QPU time twice.**

If your script stops (quota used up, laptop closed, network, Ctrl+C, crash), run the same command again:

- finished calls are **replayed instantly** from a local `.qresume` file,
- a job that was still running on IBM is **reconnected**, not resubmitted,
- only new work uses the QPU.

It works for any algorithm (VQE, QAOA, sampling sweeps, `qiskit_algorithms`, your own loops) because it
records *calls*, not algorithm state: on a rerun your code runs again from the top, gets the recorded
answers, rebuilds its own state (optimizer memory included) and continues at the first unrecorded call.
Built first for the IBM Quantum Open Plan (10 QPU minutes per rolling 28 days).

Status: 0.x - tested on simulators and on real IBM hardware ([results](#test-results-on-real-ibm-hardware));
the file format may still change between minor versions.

## Install

```bash
pip install qiskit-resume            # Qiskit >= 1.4 (tested 1.4 and 2.6), Python >= 3.10
pip install "qiskit-resume[ibm]"     # + qiskit-ibm-runtime, for IBM hardware
```

## Add it to your script

Four changes; the rest of the script stays as it is.

**Before** (plain Qiskit):

```python
from qiskit_ibm_runtime import QiskitRuntimeService, EstimatorV2
from qiskit.transpiler import generate_preset_pass_manager

service = QiskitRuntimeService()
backend = service.least_busy(operational=True, simulator=False)
pm = generate_preset_pass_manager(optimization_level=1, backend=backend)
estimator = EstimatorV2(mode=backend)

isa = pm.run(ansatz)
theta0 = np.random.default_rng().uniform(-np.pi, np.pi, ansatz.num_parameters)
res = minimize(lambda x: float(estimator.run([(isa, H.apply_layout(isa.layout), x)]).result()[0].data.evs),
               theta0, method="COBYLA")
```

**After**:

```python
from qiskit_ibm_runtime import EstimatorV2
import qiskit_resume as resume

with resume.resume_run("vqe.qresume"):                         # 1. everything runs inside this block
    backend = resume.backend()                                  # 2. device chosen once, reused on reruns
    pm = resume.pass_manager(backend, optimization_level=1)     # 3. recorded, fixed-seed transpile
    estimator = resume.wrap(EstimatorV2(mode=backend))         # 4. wrap every Estimator / Sampler

    isa = pm.run(ansatz)
    theta0 = resume.rng("init").uniform(-np.pi, np.pi, ansatz.num_parameters)  # 5. randomness that replays
    res = minimize(lambda x: float(estimator.run([(isa, H.apply_layout(isa.layout), x)]).result()[0].data.evs),
                   theta0, method="COBYLA")
```

Then check it, without using QPU time or an account:

```bash
qresume selftest my_script.py -- <your script arguments>
```

`selftest` kills and resumes the script at several points with synthetic results and reports `OK: safe to
resume` or the exact call (and source line) that would not resume. Run it after every change to the script.

### Rules (for people and for AI assistants applying this)

1. Put the code after the imports inside `with resume.resume_run("<name>.qresume"):` (primitive calls,
   `resume.rng`, `resume.seed_for` and `resume.backend` must be inside). One file per experiment, relative
   to the working directory; rerunning with the same file continues it, a new name starts from scratch.
2. Wrap **every** Estimator and Sampler with `resume.wrap(...)` (`EstimatorV2`/`SamplerV2`, the newer
   `qiskit_ibm_runtime` `Estimator`/`Sampler`, or local `StatevectorEstimator`/`StatevectorSampler`).
   Library algorithms (`qiskit_algorithms.VQE`, `QAOA`, `VQD`, `ComputeUncompute`, ...) get the wrapped
   objects passed in.
3. Replace `generate_preset_pass_manager(...)` with `resume.pass_manager(backend, ...)` (same arguments).
   For a simulator without a backend: `resume.pass_manager(optimization_level=1, basis_gates=[...])`.
   It works wherever a pass manager is accepted (e.g. `QAOA(..., transpiler=pm)`).
4. On IBM, get the device with `resume.backend(service)` (or `resume.backend(service, name="ibm_...")`)
   instead of `service.least_busy(...)`, so a rerun uses the same device. Extra filters stay as keyword
   arguments: `resume.backend(service, min_num_qubits=5)`.
5. Replace unseeded randomness that feeds the computation: `np.random.default_rng()` ->
   `resume.rng("purpose")`; `np.random.rand(k)` -> `resume.rng("purpose").random(k)`; `random.random()` ->
   `resume.rng("purpose").random()`; a random `int` seed (`seed=random.randint(...)`) ->
   `resume.seed_for("purpose")`. `resume.rng("x")` returns the same generator for the same name, so it is
   safe inside loops. Use one name per purpose. Seeded code (`default_rng(42)`) can stay.
6. Do not change anything else (removing imports that became unused is fine). Run `qresume selftest` and
   fix what it reports. Library-internal randomness (e.g. SPSA in `qiskit_algorithms`) needs nothing.
7. Optional: decorate a slow, deterministic classical function with `@resume.wrap` so reruns skip it
   (classical code otherwise reruns on every replay - correct, just slower). The end-of-run report lists
   slow sections worth wrapping.

## API

| call | what it does |
|---|---|
| `resume.resume_run(path, **settings)` | the experiment context; settings: `qpu_budget_s`, `job_wait_timeout_s`, `job_max_retries`, `on_divergence`, `on_backend_change`, `seed`, `force`, `log_calls`, ... |
| `resume.wrap(estimator_or_sampler)` | records each call: question hash, IBM job id (before waiting), result, QPU seconds; replays/reconnects on rerun |
| `resume.pass_manager(backend, **preset_args)` | recorded preset pass manager with a fixed `seed_transpiler`; same circuit on every rerun |
| `resume.wrap(pm, backend=backend)` | the same for a pass manager you built yourself |
| `resume.backend(service=None, name=None)` | the experiment's IBM device: least busy (or `name`) on the first run, the recorded one afterwards |
| `resume.rng(name=None, *, seed=None)` | numpy `Generator` that replays: one generator per name (order-independent, safe in loops), a new one per unnamed call (by call order), or from an explicit seed |
| `resume.seed_for(name)` / `resume.seed` | stable `int` seed per name / the experiment seed |
| `@resume.wrap` on a function | records a slow, side-effect-free function's return value per arguments |
| `run.snapshot({...})` / `run.load_snapshot()` | optional: skip replaying the start of a very long run |

Other behaviour: changed code is detected and reported (`on_divergence="stop"` to stop instead of
continuing); a device change is detected (`on_backend_change="stop"`); `qpu_budget_s` stops cleanly before
the job that would exceed it; global `random`/`numpy` and `qiskit_algorithms.algorithm_globals` are seeded.

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

## Test results on real IBM hardware

ibm_kingston, IBM Quantum Open Plan, 2026-10-24/25. Reproduce with
[`examples/ibm/`](https://github.com/set3523/Qiskit-resume/tree/master/examples/ibm).

| # | What was checked | Workload | Result |
|---|---|---|---|
| 1 | Estimator loop, hard kill, rerun: recorded calls replay from the file | VQE (EstimatorV2 + COBYLA) | PASS |
| 2 | Rerun reconnects to the job submitted before the kill (still running / already finished), no resubmission | VQE, QAOA, checks 6, 9 | PASS |
| 3 | QPU budget stops the run before the job that would exceed it | VQE | PASS |
| 4 | Sampler loop, hard kill, reconnect, run to completion | QAOA Max-Cut (SamplerV2 + COBYLA) | PASS |
| 5 | Jobs with several pubs and parameter sweeps replay bit-for-bit | Estimator 2 pubs (broadcast), Sampler 2 circuits (sweep) | PASS |
| 6 | Kill right after submit, rerun reconnects to the same job id | Estimator | PASS |
| 7 | Wait timeout stops cleanly, rerun reconnects to the waiting job | Estimator | PASS |
| 8 | Device change (ibm_kingston -> ibm_fez) stops before any submission | Estimator | PASS |
| 9 | New client-side `Estimator`/`Sampler` (qiskit-ibm-runtime >= 0.50): reconnect and replay | Estimator + Sampler | PASS |
| - | IBM-side job failure and automatic retry | - | not seen yet (tested with a fake service) |
| - | Real quota-exhausted stop | - | not seen yet (tested with a fake service) |
| - | Reconnect while the job is still QUEUED | - | not seen yet (queue was empty; same code path as above) |

Observed QPU cost per job on ibm_kingston: Sampler about 2 s, Estimator about 11-13 s (default error mitigation).

## Limits

- A single circuit execution cannot be paused (quantum states can't be saved); interruption
  granularity is one primitive call.
- Code that is not deterministic in ways qiskit-resume can't seed (time, external data) may
  diverge on replay — you'll be told where.
- Calls not made through Primitives (`backend.run`, direct `Statevector` maths) are not recorded.

## Documentation

[DECISIONS](https://github.com/set3523/Qiskit-resume/blob/master/docs/DECISIONS.md) (why) ·
[ARCHITECTURE](https://github.com/set3523/Qiskit-resume/blob/master/docs/ARCHITECTURE.md) (layers and flow) ·
[CONTRACTS](https://github.com/set3523/Qiskit-resume/blob/master/docs/CONTRACTS.md) (every module's inputs/outputs)

## Support and maintenance

qiskit-resume is actively maintained by [@set3523](https://github.com/set3523) as an individual open-source project.

- **Questions, bugs, feature requests:** open a [GitHub issue](https://github.com/set3523/Qiskit-resume/issues).
  For bugs, please include the `qresume status <file>` output and the Qiskit / qiskit-ibm-runtime versions.
- **Other contact** (collaboration, research use): set35230@gmail.com
- **Response:** best effort, usually within a few days; no guaranteed response time.
- **Supported versions:** Python >= 3.10, Qiskit >= 1.4 (tested on 1.4 and 2.x), the latest
  qiskit-ibm-runtime. Fixes go into the latest 0.x release only.
- **Compatibility:** while the version is 0.x, the `.qresume` file format may change between minor
  versions; such changes are listed in the release notes.
- **Contributing:** pull requests are welcome; please open an issue first for larger changes.

License: MIT
