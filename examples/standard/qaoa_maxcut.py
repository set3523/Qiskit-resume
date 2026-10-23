"""Standard Qiskit workload (plain Qiskit, no qiskit-resume inside):
Max-Cut on a random 3-regular graph with qiskit_algorithms.QAOA + COBYLA.

    python qaoa_maxcut.py                       # plain run, no protection
    python ../crash_and_resume.py qaoa_maxcut.py      # wrapped, killed half way, resumed

Defaults (16 nodes, reps=5, COBYLA up to 300 iterations) run for several minutes.
The ansatz is decomposed to basic gates by a pass manager (as on hardware); without it the
simulator would build the full 2^n x 2^n evolution matrix of the cost operator.

Interface used by crash_and_resume.py: SEED, build_primitives(seed), main(**primitives, **params).
"""

import numpy as np
from qiskit.primitives import StatevectorSampler
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler import generate_preset_pass_manager
from qiskit_algorithms import QAOA
from qiskit_algorithms.optimizers import COBYLA

SEED = 1234


def build_primitives(seed: int = SEED) -> dict:
    return {
        "sampler": StatevectorSampler(default_shots=2000, seed=seed),
        "pass_manager": generate_preset_pass_manager(optimization_level=1, basis_gates=["rz", "rzz", "sx", "x", "cx"],
                                                     seed_transpiler=seed),
    }


def random_3_regular(n_nodes: int, seed: int) -> list:
    """Configuration-model 3-regular graph (retries until there are no loops or multi-edges)."""
    if n_nodes < 4 or n_nodes % 2:
        raise ValueError("a 3-regular graph needs an even number of nodes >= 4")
    g = np.random.default_rng(seed)
    while True:
        stubs = g.permutation(np.repeat(np.arange(n_nodes), 3))
        pairs = [tuple(sorted(p)) for p in stubs.reshape(-1, 2)]
        if all(a != b for a, b in pairs) and len(set(pairs)) == len(pairs):
            return pairs


def main(sampler, pass_manager, *, num_nodes: int = 16, reps: int = 5, maxiter: int = 300, seed: int = SEED) -> dict:
    edges = random_3_regular(num_nodes, seed=7)  # fixed problem instance
    cost = SparsePauliOp.from_sparse_list([("ZZ", [i, j], 0.5) for i, j in edges], num_qubits=num_nodes)

    def cut_value(bits: str) -> int:
        x = bits[::-1]
        return sum(x[i] != x[j] for i, j in edges)

    def callback(count, params, value, meta):
        if count % 20 == 0:
            print(f"  evaluation {count}: cost {value:.6f}", flush=True)

    qaoa = QAOA(sampler, COBYLA(maxiter=maxiter), reps=reps,
                initial_point=np.random.default_rng(seed).uniform(0, np.pi, 2 * reps),
                transpiler=pass_manager, callback=callback)
    result = qaoa.compute_minimum_eigenvalue(cost)
    best = result.best_measurement["bitstring"]
    print(f"best cut {best}: value {cut_value(best)} of {len(edges)} edges "
          f"({result.cost_function_evals} evaluations)")
    return {"bitstring": best, "cut": cut_value(best), "evaluations": int(result.cost_function_evals)}


if __name__ == "__main__":
    main(**build_primitives(SEED))
