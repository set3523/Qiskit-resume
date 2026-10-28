"""04 - a slow classical step wrapped as a function, plus snapshots.

    python 04_snapshot_and_function.py [file.qresume]

* ``@resume.wrap`` on a slow, side-effect-free function: its return value is recorded,
  so replays skip the slow part.
* ``run.snapshot(state)``: save the loop's full state now and then; a rerun starts at the
  latest snapshot instead of replaying from the start. Put *every* variable the loop needs
  into the snapshot - if something is missing, qiskit-resume detects it and says so.
"""

import sys
import time

import numpy as np
try:
    from qiskit.circuit.library import real_amplitudes  # Qiskit >= 2.1
except ImportError:
    from qiskit.circuit.library import RealAmplitudes as real_amplitudes
from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import SparsePauliOp

import qiskit_resume as resume

path = sys.argv[1] if len(sys.argv) > 1 else "04_snapshot_and_function.qresume"


@resume.wrap
def build_hamiltonian(n_qubits: int) -> dict:
    time.sleep(1.0)  # stands in for e.g. a long PySCF integral calculation
    terms = [("Z" * n_qubits, 1.0), ("X" + "I" * (n_qubits - 1), 0.4)]
    return {"paulis": [t for t, _ in terms], "coeffs": np.array([c for _, c in terms])}


ansatz = real_amplitudes(3, reps=1)
estimator = resume.wrap(StatevectorEstimator())

with resume.resume_run(path) as run:
    h = build_hamiltonian(3)
    hamiltonian = SparsePauliOp(h["paulis"], h["coeffs"])

    snap = run.load_snapshot()
    if snap:
        step, theta, velocity = snap["step"], snap["theta"], snap["velocity"]
    else:
        step, theta, velocity = 0, resume.rng().normal(size=ansatz.num_parameters), np.zeros(ansatz.num_parameters)

    for step in range(step, 30):
        # momentum gradient descent with a finite-difference gradient (2 Estimator calls per parameter)
        grad = np.zeros_like(theta)
        for k in range(len(theta)):
            shift = np.eye(len(theta))[k] * 0.1
            plus, minus = estimator.run([(ansatz, hamiltonian, theta + shift), (ansatz, hamiltonian, theta - shift)]).result()
            grad[k] = (float(plus.data.evs) - float(minus.data.evs)) / 0.2
        velocity = 0.8 * velocity - 0.1 * grad
        theta = theta + velocity
        if step % 5 == 4:
            run.snapshot({"step": step + 1, "theta": theta, "velocity": velocity})

    e = float(estimator.run([(ansatz, hamiltonian, theta)]).result()[0].data.evs)
    print(f"final energy {e:.6f}; this run: {run.stats}")
