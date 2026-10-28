"""01 - the smallest example: a loop of Estimator calls.

    python 01_estimator_loop.py [file.qresume]

Press Ctrl+C half way, run the same command again: finished calls are replayed
instantly from the file and the loop continues where it stopped.
"""

import sys
import time

import numpy as np
from qiskit.circuit import Parameter, QuantumCircuit
from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import SparsePauliOp

import qiskit_resume as resume

path = sys.argv[1] if len(sys.argv) > 1 else "01_estimator_loop.qresume"
slow = "--slow" in sys.argv

theta = Parameter("theta")
qc = QuantumCircuit(1)
qc.ry(theta, 0)
observable = SparsePauliOp("Z")

estimator = resume.wrap(StatevectorEstimator())  # the only change to plain Qiskit code

with resume.resume_run(path):
    for angle in np.linspace(0, np.pi, 8):
        result = estimator.run([(qc, observable, [angle])]).result()
        print(f"angle={angle:.3f}  <Z>={float(result[0].data.evs):+.4f}")
        if slow:
            time.sleep(1)  # gives you time to press Ctrl+C
