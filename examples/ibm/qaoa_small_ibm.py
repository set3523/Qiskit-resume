"""Small QAOA (Max-Cut) on real IBM hardware: the Sampler counterpart of vqe_small_ibm.py
(plain Qiskit in the style of IBM's QAOA tutorial: QAOA ansatz + IBM SamplerV2 + scipy COBYLA,
no qiskit-resume inside). The cost is computed from the sampled bitstrings, so every optimizer
step is one Sampler job, plus one final sampling job.

(qiskit_algorithms.QAOA is not used here: it does not map the cost operator onto the device
layout, so it fails as soon as the device has more qubits than the problem.)

Default: Max-Cut on a 4-node ring, reps=1, COBYLA up to 10 iterations -> about 11 Sampler jobs.
Each job also waits in the public queue, so wall time can be much longer than QPU time.

    $env:QISKIT_IBM_DEVICE = "ibm_xxx"                  # pin the device (PowerShell)
    python ../crash_and_resume.py ibm/qaoa_small_ibm.py --estimate                      # count jobs (no QPU)
    python ../crash_and_resume.py ibm/qaoa_small_ibm.py --kill-at-call 3 --kill-delay 20 --qpu-budget 60

QISKIT_IBM_DEVICE: a device name, "fake" (local noisy simulator, no account), or unset (least busy).

Interface used by crash_and_resume.py: SEED, build_primitives(seed), main(**primitives, **params).
"""

import os

import numpy as np
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler import generate_preset_pass_manager
from scipy.optimize import minimize

try:
    from qiskit.circuit.library import qaoa_ansatz  # Qiskit >= 2.1
except ImportError:
    from qiskit.circuit.library import QAOAAnsatz as qaoa_ansatz

SEED = 1234
SHOTS = 1000


def get_backend():
    name = os.environ.get("QISKIT_IBM_DEVICE", "").strip()
    if name == "fake":
        from qiskit_ibm_runtime.fake_provider import FakeManilaV2

        return FakeManilaV2()
    from qiskit_ibm_runtime import QiskitRuntimeService

    service = QiskitRuntimeService()
    return service.backend(name) if name else service.least_busy(operational=True, simulator=False)


def build_primitives(seed: int = SEED) -> dict:
    from qiskit_ibm_runtime import SamplerV2

    backend = get_backend()
    print(f"device: {backend.name}", flush=True)
    return {
        "backend": backend,  # not a primitive: tells crash_and_resume.py which device the transpile is for
        "sampler": SamplerV2(mode=backend, options={"default_shots": SHOTS}),
        "pass_manager": generate_preset_pass_manager(backend=backend, optimization_level=1, seed_transpiler=seed),
    }


def main(sampler, pass_manager, *, num_nodes: int = 4, reps: int = 1, maxiter: int = 10, seed: int = SEED) -> dict:
    edges = [(i, (i + 1) % num_nodes) for i in range(num_nodes)]  # ring graph
    cost = SparsePauliOp.from_sparse_list([("ZZ", [i, j], 0.5) for i, j in edges], num_qubits=num_nodes)
    ansatz = qaoa_ansatz(cost, reps=reps)
    ansatz.measure_all()
    isa = pass_manager.run(ansatz)  # measured bits stay in problem order, so no layout mapping is needed

    def cut_value(bits: str) -> int:
        x = bits[::-1]
        return sum(x[i] != x[j] for i, j in edges)

    def sample(theta):
        return sampler.run([(isa, theta)]).result()[0].data.meas.get_counts()

    count = 0

    def energy(theta):  # <cost> = sum_edges 0.5 z_i z_j = 0.5 * (#edges - 2 * cut)
        nonlocal count
        count += 1
        counts = sample(theta)
        shots = sum(counts.values())
        e = sum(c * 0.5 * (len(edges) - 2 * cut_value(b)) for b, c in counts.items()) / shots
        print(f"  job {count}: cost {e:.4f}", flush=True)
        return e

    theta0 = np.random.default_rng(seed).uniform(0, np.pi, ansatz.num_parameters)
    res = minimize(energy, theta0, method="COBYLA", options={"maxiter": maxiter})
    final = sample(res.x)  # one more job: sample the optimized circuit
    best = max(final, key=lambda b: (cut_value(b), final[b]))
    optimum = len(edges) if num_nodes % 2 == 0 else len(edges) - 1
    print(f"best cut {best}: value {cut_value(best)} of {len(edges)} edges (optimum {optimum}; "
          f"{res.nfev} optimizer jobs + 1 final)")
    return {"bitstring": best, "cut": cut_value(best), "jobs": int(res.nfev) + 1}


if __name__ == "__main__":
    p = build_primitives(SEED)
    main(p["sampler"], p["pass_manager"])
