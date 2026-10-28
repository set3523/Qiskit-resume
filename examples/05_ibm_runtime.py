"""05 - template for IBM Quantum (needs an IBM account; uses QPU time).

    pip install "qiskit-resume[ibm]"
    qresume selftest 05_ibm_runtime.py -- vqe_ibm.qresume     # check resumability first (no QPU time)
    python 05_ibm_runtime.py vqe_ibm.qresume

If the run stops (quota used up, PC off, network, Ctrl+C), run the same command
again later. Finished jobs are replayed from the file, a job that was still running
on IBM is reconnected (not resubmitted), and only new work uses QPU time.
"""

import sys

import numpy as np
from scipy.optimize import minimize
try:
    from qiskit.circuit.library import real_amplitudes  # Qiskit >= 2.1
except ImportError:
    from qiskit.circuit.library import RealAmplitudes as real_amplitudes
from qiskit.quantum_info import SparsePauliOp
from qiskit_ibm_runtime import EstimatorV2

import qiskit_resume as resume

path = sys.argv[1] if len(sys.argv) > 1 else "vqe_ibm.qresume"
device = sys.argv[2] if len(sys.argv) > 2 else None   # optional: a device name for the first run

ansatz = real_amplitudes(4, reps=1)
hamiltonian = SparsePauliOp.from_list([("ZZII", 1.0), ("IZZI", 1.0), ("IIZZ", 1.0), ("XXXX", 0.5)])

with resume.resume_run(path, qpu_budget_s=120) as run:      # stop cleanly after 2 QPU minutes this run
    backend = resume.backend(name=device)                    # least busy (or `device`) once; reruns reuse it
    pm = resume.pass_manager(backend, optimization_level=1)  # recorded: same layout on every replay
    isa = pm.run(ansatz)
    isa_ham = hamiltonian.apply_layout(isa.layout)
    estimator = resume.wrap(EstimatorV2(mode=backend))

    def energy(theta):
        return float(estimator.run([(isa, isa_ham, theta)]).result()[0].data.evs)

    theta0 = resume.rng("init").uniform(-np.pi, np.pi, ansatz.num_parameters)
    res = minimize(energy, theta0, method="COBYLA", options={"maxiter": 50})
    print(f"energy {res.fun:.4f}; QPU used this run: {run.usage().used_run_s:.1f}s; {run.stats}")
