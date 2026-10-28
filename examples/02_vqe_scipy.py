"""02 - VQE with a real optimizer (scipy COBYLA), resumable without saving optimizer state.

    python 02_vqe_scipy.py [file.qresume]

The optimizer's memory is rebuilt by replay: on a rerun it receives the recorded
energies, makes the same decisions, and continues at the first unrecorded call.
On IBM hardware, replace StatevectorEstimator() with EstimatorV2(mode=backend) and
transpile with a wrapped pass manager (see 05_ibm_runtime.py).
"""

import sys

import numpy as np
from scipy.optimize import minimize
try:
    from qiskit.circuit.library import real_amplitudes  # Qiskit >= 2.1
except ImportError:
    from qiskit.circuit.library import RealAmplitudes as real_amplitudes
from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import SparsePauliOp

import qiskit_resume as resume

path = sys.argv[1] if len(sys.argv) > 1 else "02_vqe_scipy.qresume"
maxiter = int(sys.argv[2]) if len(sys.argv) > 2 else 80

ansatz = real_amplitudes(2, reps=2)
hamiltonian = SparsePauliOp.from_list([("XX", 0.5), ("YY", 0.5), ("ZZ", 0.5), ("ZI", 0.2)])
estimator = resume.wrap(StatevectorEstimator())

with resume.resume_run(path) as run:
    theta0 = resume.rng().uniform(-np.pi, np.pi, ansatz.num_parameters)  # random, reproduced on replay

    def energy(theta):
        return float(estimator.run([(ansatz, hamiltonian, theta)]).result()[0].data.evs)

    res = minimize(energy, theta0, method="COBYLA", options={"maxiter": maxiter})
    print(f"energy = {res.fun:.6f} after {res.nfev} evaluations")
    print(f"this run: {run.stats}")
