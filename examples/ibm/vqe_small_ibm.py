"""Small VQE on real IBM hardware, written the way IBM's own tutorials do it
(plain Qiskit: EstimatorV2 + scipy COBYLA, no qiskit-resume inside).

About 15 Estimator jobs on 2 qubits; on an Open Plan account this uses roughly 1 QPU minute,
but each job also waits in the public queue, so wall time can be much longer.

    $env:QISKIT_IBM_DEVICE = "ibm_xxx"                  # pin the device (PowerShell); see below
    python vqe_small_ibm.py                             # plain run, no protection
    python ../crash_and_resume.py ibm/vqe_small_ibm.py --kill-at-call 6 --kill-delay 20 --qpu-budget 120

QISKIT_IBM_DEVICE:
    a device name   use that device (keep it the same between runs)
    "fake"          local noisy simulator (FakeManilaV2), no account, no QPU time - for a dry run
    unset           least busy device (printed; set it before resuming so the device does not change)

Interface used by crash_and_resume.py: SEED, build_primitives(seed), main(**primitives, **params).
"""

import os

import numpy as np
from qiskit.quantum_info import SparsePauliOp
from qiskit.transpiler import generate_preset_pass_manager
from scipy.optimize import minimize

try:
    from qiskit.circuit.library import real_amplitudes  # Qiskit >= 2.1
except ImportError:
    from qiskit.circuit.library import RealAmplitudes as real_amplitudes

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
    from qiskit_ibm_runtime import EstimatorV2

    backend = get_backend()
    print(f"device: {backend.name}", flush=True)
    return {
        "backend": backend,  # not a primitive: tells crash_and_resume.py which device the transpile is for
        "estimator": EstimatorV2(mode=backend, options={"default_shots": SHOTS}),
        "pass_manager": generate_preset_pass_manager(backend=backend, optimization_level=1, seed_transpiler=seed),
    }


def main(estimator, pass_manager, *, maxiter: int = 15, seed: int = SEED) -> dict:
    ansatz = real_amplitudes(2, reps=1)
    hamiltonian = SparsePauliOp.from_list([("ZZ", 1.0), ("XI", 0.5), ("IX", 0.5)])
    isa = pass_manager.run(ansatz)                      # circuits must match the device (ISA)
    isa_ham = hamiltonian.apply_layout(isa.layout)
    count = 0

    def energy(theta):
        nonlocal count
        count += 1
        e = float(estimator.run([(isa, isa_ham, theta)]).result()[0].data.evs)
        print(f"  job {count}: energy {e:.4f}", flush=True)
        return e

    theta0 = np.random.default_rng(seed).uniform(-np.pi, np.pi, ansatz.num_parameters)
    res = minimize(energy, theta0, method="COBYLA", options={"maxiter": maxiter})
    print(f"VQE energy {res.fun:.4f} after {res.nfev} jobs (exact ground energy: "
          f"{min(np.linalg.eigvalsh(hamiltonian.to_matrix())):.4f})")
    return {"energy": float(res.fun), "jobs": int(res.nfev)}


if __name__ == "__main__":
    p = build_primitives(SEED)
    main(p["estimator"], p["pass_manager"])
