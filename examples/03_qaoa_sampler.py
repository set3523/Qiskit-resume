"""03 - QAOA for Max-Cut with Sampler + a cached transpile step.

    python 03_qaoa_sampler.py [file.qresume]
"""

import sys

import numpy as np
from scipy.optimize import minimize
from qiskit.circuit import ParameterVector, QuantumCircuit
from qiskit.primitives import StatevectorSampler
from qiskit.transpiler import generate_preset_pass_manager

import qiskit_resume as resume

path = sys.argv[1] if len(sys.argv) > 1 else "03_qaoa_sampler.qresume"
edges = [(0, 1), (1, 2), (2, 0), (2, 3)]
n, p = 4, 2

gamma, beta = ParameterVector("g", p), ParameterVector("b", p)
qc = QuantumCircuit(n)
qc.h(range(n))
for layer in range(p):
    for i, j in edges:
        qc.rzz(2 * gamma[layer], i, j)
    qc.rx(2 * beta[layer], range(n))
qc.measure_all()


def cut_value(bits: str) -> int:
    x = bits[::-1]
    return sum(x[i] != x[j] for i, j in edges)


with resume.resume_run(path) as run:
    pm = resume.wrap(generate_preset_pass_manager(optimization_level=1, basis_gates=["rz", "sx", "x", "cx"],
                                                  seed_transpiler=resume.seed), backend="local")
    isa = pm.run(qc)                                        # cached: not transpiled again on replay
    sampler = resume.wrap(StatevectorSampler(seed=resume.seed))

    def cost(x):
        counts = sampler.run([(isa, x)], shots=500).result()[0].data.meas.get_counts()
        return -sum(cut_value(b) * c for b, c in counts.items()) / 500

    x0 = resume.rng().uniform(0, np.pi, 2 * p)
    res = minimize(cost, x0, method="COBYLA", options={"maxiter": 40})
    best = sampler.run([(isa, res.x)], shots=1000).result()[0].data.meas.get_counts()
    top = max(best, key=best.get)
    print(f"best cut {top} (value {cut_value(top)}), expected cut {-res.fun:.3f}")
    print(f"this run: {run.stats}")
