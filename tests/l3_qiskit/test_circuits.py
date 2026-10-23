import numpy as np
from qiskit.circuit import Parameter, QuantumCircuit
try:
    from qiskit.circuit.library import real_amplitudes  # Qiskit >= 2.1
except ImportError:
    from qiskit.circuit.library import RealAmplitudes as real_amplitudes
from qiskit.quantum_info import SparsePauliOp

from qiskit_resume.l1_record.hashing import diff_parts, digest_parts
from qiskit_resume.l3_qiskit.circuits import circuit_from_qpy, circuit_to_qpy, question_parts


def build(theta_name="t"):
    t = Parameter(theta_name)
    qc = QuantumCircuit(2)
    qc.ry(t, 0)
    qc.cx(0, 1)
    return qc


def key(qc, vals, obs="ZZ"):
    return digest_parts(question_parts("estimator", [(qc, SparsePauliOp(obs), vals)]))


def test_same_question_from_fresh_objects_same_key():
    # new Parameter objects get new UUIDs every run - must not change the key
    assert key(build(), [0.3]) == key(build(), [0.3])


def test_tiny_float_noise_ignored_real_change_detected():
    assert key(build(), [0.1 + 0.2]) == key(build(), [0.3])
    a, b = key(build(), [0.3]), key(build(), [0.31])
    assert diff_parts(a, b) == ["pub0.params"]
    assert diff_parts(key(build(), [0.3]), key(build(), [0.3], "XX")) == ["pub0.observables"]


def test_circuit_change_detected():
    qc2 = build()
    qc2.h(1)
    assert diff_parts(key(build(), [0.3]), key(qc2, [0.3])) == ["pub0.circuit"]


def test_sampler_parts_and_qpy_roundtrip():
    qc = real_amplitudes(2, reps=1)
    qc.measure_all()
    parts = question_parts("sampler", [(qc, np.zeros(qc.num_parameters))], shots=100)
    assert set(parts) == {"kind", "pub0.circuit", "pub0.params", "pub0.shots"}
    back = circuit_from_qpy(circuit_to_qpy(qc))
    assert back == qc
