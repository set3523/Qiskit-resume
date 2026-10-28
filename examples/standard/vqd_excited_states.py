"""Standard Qiskit workload (plain Qiskit, no qiskit-resume inside):
lowest states of a Heisenberg chain with qiskit_algorithms.VQD.

VQD calls an Estimator for energies and a fidelity routine (ComputeUncompute) that calls a
Sampler internally, so library-internal calls are protected too when the primitives are wrapped.

    python vqd_excited_states.py                # plain run, no protection
    python ../crash_and_resume.py vqd_excited_states.py   # wrapped, killed half way, resumed

Interface used by crash_and_resume.py: SEED, build_primitives(seed), main(**primitives, **params).
"""

import numpy as np
from qiskit.primitives import StatevectorEstimator, StatevectorSampler
from qiskit.quantum_info import SparsePauliOp
from qiskit_algorithms import VQD
from qiskit_algorithms.optimizers import COBYLA
from qiskit_algorithms.state_fidelities import ComputeUncompute

try:
    from qiskit.circuit.library import real_amplitudes  # Qiskit >= 2.1
except ImportError:
    from qiskit.circuit.library import RealAmplitudes as real_amplitudes

SEED = 1234


def build_primitives(seed: int = SEED) -> dict:
    return {"estimator": StatevectorEstimator(seed=seed),
            "sampler": StatevectorSampler(default_shots=4000, seed=seed)}


def main(estimator, sampler, *, num_qubits: int = 8, k: int = 3, maxiter: int = 250, seed: int = SEED) -> dict:
    n = num_qubits
    terms = [("I" * i + p + p + "I" * (n - i - 2), 1.0) for i in range(n - 1) for p in "XYZ"]
    hamiltonian = SparsePauliOp.from_list(terms)
    ansatz = real_amplitudes(n, reps=2)
    g = np.random.default_rng(seed)

    def callback(count, params, value, meta, step):
        if count % 50 == 0:
            print(f"  state {step - 1}, evaluation {count}: value {value:.6f}", flush=True)

    vqd = VQD(estimator, ComputeUncompute(sampler), ansatz, COBYLA(maxiter=maxiter), k=k, betas=[3.0] * k,
              initial_point=[g.uniform(-0.5, 0.5, ansatz.num_parameters) for _ in range(k)], callback=callback)
    result = vqd.compute_eigenvalues(hamiltonian)
    energies = [float(e.real) for e in result.eigenvalues]
    for i, e in enumerate(energies):
        print(f"state {i}: energy {e:.6f}")
    return {"energies": energies}


if __name__ == "__main__":
    main(**build_primitives(SEED))
