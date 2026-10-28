"""Standard Qiskit workload (plain Qiskit, no qiskit-resume inside):
ground state of a transverse-field Ising chain with qiskit_algorithms.VQE + SPSA.

    python vqe_ising_spsa.py                    # plain run, no protection
    python ../crash_and_resume.py vqe_ising_spsa.py   # wrapped, killed half way, resumed

Defaults (14 qubits, 1000 SPSA iterations -> about 2000 Estimator calls) take several minutes.

Interface used by crash_and_resume.py:
    SEED                      fixed seed (makes the run repeatable)
    build_primitives(seed)    -> dict of the primitives this workload uses
    main(**primitives, **params) -> dict of results
"""

from qiskit.primitives import StatevectorEstimator
from qiskit.quantum_info import SparsePauliOp
from qiskit_algorithms import VQE
from qiskit_algorithms.optimizers import SPSA
from qiskit_algorithms.utils import algorithm_globals

try:
    from qiskit.circuit.library import efficient_su2  # Qiskit >= 2.1
except ImportError:
    from qiskit.circuit.library import EfficientSU2 as efficient_su2

import numpy as np

SEED = 1234


def build_primitives(seed: int = SEED) -> dict:
    return {"estimator": StatevectorEstimator(seed=seed)}


def main(estimator, *, num_qubits: int = 14, maxiter: int = 1000, seed: int = SEED) -> dict:
    algorithm_globals.random_seed = seed  # SPSA draws its perturbations from here
    n = num_qubits
    # H = -J sum Z_i Z_{i+1} - h sum X_i  (J = 1, h = 1: the critical point)
    terms = [("I" * i + "ZZ" + "I" * (n - i - 2), -1.0) for i in range(n - 1)]
    terms += [("I" * i + "X" + "I" * (n - i - 1), -1.0) for i in range(n)]
    hamiltonian = SparsePauliOp.from_list(terms)
    ansatz = efficient_su2(n, su2_gates=["ry"], entanglement="linear", reps=3)

    def callback(count, params, value, meta):
        if count % 50 == 0:
            print(f"  evaluation {count}: energy {value:.6f}", flush=True)

    vqe = VQE(estimator, ansatz, SPSA(maxiter=maxiter),
              initial_point=np.random.default_rng(seed).uniform(-0.1, 0.1, ansatz.num_parameters),
              callback=callback)
    result = vqe.compute_minimum_eigenvalue(hamiltonian)
    print(f"VQE energy: {result.eigenvalue.real:.6f}  ({result.cost_function_evals} evaluations)")
    return {"energy": float(result.eigenvalue.real), "evaluations": int(result.cost_function_evals)}


if __name__ == "__main__":
    main(**build_primitives(SEED))
