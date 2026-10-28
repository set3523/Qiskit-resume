# examples

| file | what it shows | runs on |
|---|---|---|
| `01_estimator_loop.py` | smallest example: a loop of Estimator calls | local |
| `02_vqe_scipy.py` | VQE with scipy COBYLA, resumed without saving optimizer state | local |
| `03_qaoa_sampler.py` | QAOA Max-Cut with Sampler and a cached transpile step | local |
| `04_snapshot_and_function.py` | slow classical step with `@resume.wrap`, plus snapshots | local |
| `05_ibm_runtime.py` | IBM Quantum template (needs an account and QPU time) | IBM |

## Standard Qiskit workloads + crash/resume runner

`standard/` holds **plain Qiskit code** (unmodified `qiskit_algorithms` classes, no qiskit-resume inside).
Each runs on its own: `python standard/vqe_ising_spsa.py`.

`crash_and_resume.py` loads one of them, wraps its primitives with qiskit-resume, **hard-kills it part way**,
then runs it again; every quantum call is logged to the console. With `--compare` it also does a clean run
and checks that the results are identical. Needs `pip install qiskit-algorithms scipy`.

| file | workload | parameters (defaults) |
|---|---|---|
| `standard/vqe_ising_spsa.py` | VQE + SPSA, transverse-field Ising chain, EfficientSU2 | `num_qubits=14 maxiter=1000` |
| `standard/qaoa_maxcut.py` | QAOA + COBYLA, Max-Cut on a random 3-regular graph | `num_nodes=16 reps=5 maxiter=300` |
| `standard/vqd_excited_states.py` | VQD, Heisenberg chain, fidelity via Sampler | `num_qubits=8 k=3 maxiter=250` |

```bash
python crash_and_resume.py standard/vqe_ising_spsa.py                         # full size, killed 10 s in
python crash_and_resume.py standard/qaoa_maxcut.py --kill-at-call 100 --compare
python crash_and_resume.py standard/vqd_excited_states.py --set num_qubits=4 --set k=2 --kill-after 3 --compare
```

Options: `--kill-at-call N` or `--kill-after SEC` (default 10 s after the first call), `--set key=value`
(example parameter), `--compare`, `--file F` (default `<example>.demo.qresume`, recreated each time).
To add your own workload, give the file `SEED`, `build_primitives(seed)` and `main(**primitives, **params)`.

## Real IBM hardware (`ibm/`)

`ibm/vqe_small_ibm.py`: a 2-qubit VQE in IBM-tutorial style (EstimatorV2 + scipy COBYLA, ~15 jobs, about 1 QPU minute).
Set `QISKIT_IBM_DEVICE` to a device name (or `fake` for a free local dry run), then:

```bash
python crash_and_resume.py ibm/vqe_small_ibm.py --kill-at-call 6 --kill-delay 20 --qpu-budget 120
```

`--kill-delay 20` kills run 1 twenty seconds after job 6 is sent, i.e. while it waits on IBM, so run 2 must
**reconnect** to that job instead of submitting it again (`call #6 estimator: reattach` in the log).
`--qpu-budget 120` stops cleanly before a run would use more than 2 QPU minutes.

Before spending QPU time, count the jobs a configuration makes (synthetic results, no QPU, no queue):

```bash
python crash_and_resume.py ibm/vqe_small_ibm.py --set maxiter=300 --set num_qubits=4 --set reps=3 --estimate
```

After a budget stop, continue later with `--file <record> --no-kill` (one normal run on the same record).

`ibm/qaoa_small_ibm.py`: the Sampler counterpart (QAOA Max-Cut on a 4-node ring, IBM-tutorial style, ~11 jobs):

```bash
python crash_and_resume.py ibm/qaoa_small_ibm.py --kill-at-call 3 --kill-delay 20 --qpu-budget 45
```

`ibm/hardware_checks.py`: short hardware checks with PASS/FAIL/SKIP verdicts (about 6 jobs in total):
multi-pub and sweep jobs replay identically (5), reconnect after a kill right after submit (6),
clean stop on a wait timeout and reconnect (7), device change stops before any submit (8), and the
new client-side Estimator/Sampler of qiskit-ibm-runtime >= 0.50 (9).

```bash
python ibm/hardware_checks.py
```

Note: `--compare` is only meaningful on deterministic simulators. On hardware (and on noisy fake
devices) every fresh job samples new noise, so a resumed run and a separate clean run differ anyway.

## Interrupt and resume

```bash
pip install -e ..                                   # once, from the ver2 folder
python 01_estimator_loop.py demo.qresume --slow     # press Ctrl+C half way
python 01_estimator_loop.py demo.qresume --slow     # same command: finished calls replay instantly, the rest continues
qresume status demo.qresume
```

## Before using IBM

```bash
qresume selftest 02_vqe_scipy.py -- any.qresume                       # crash/resume at several points (no QPU)
qresume check 02_vqe_scipy.py --file demo.qresume -- demo.qresume     # can this file continue? (no IBM contact)
```
