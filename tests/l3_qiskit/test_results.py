import numpy as np
from qiskit.circuit import Parameter, QuantumCircuit
from qiskit.primitives import StatevectorEstimator, StatevectorSampler
from qiskit.quantum_info import SparsePauliOp

from qiskit_resume.l0_disk.codec import decode, encode
from qiskit_resume.l3_qiskit.results import from_neutral, to_neutral


def test_estimator_roundtrip_bit_exact():
    t = Parameter("t")
    qc = QuantumCircuit(1)
    qc.ry(t, 0)
    r = StatevectorEstimator().run([(qc, SparsePauliOp("Z"), [[0.1], [0.7]])]).result()
    back = from_neutral(decode(encode(to_neutral(r, kind="estimator"))))
    assert back[0].data.evs.tobytes() == r[0].data.evs.tobytes()
    assert back[0].data.stds.tobytes() == r[0].data.stds.tobytes()
    assert back[0].metadata == r[0].metadata and back.metadata == r.metadata


def test_sampler_roundtrip_raw_and_counts():
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    r = StatevectorSampler(seed=1).run([(qc,)], shots=64).result()
    raw = from_neutral(decode(encode(to_neutral(r, kind="sampler"))))
    assert np.array_equal(raw[0].data.meas.array, r[0].data.meas.array)
    assert raw[0].data.meas.get_bitstrings() == r[0].data.meas.get_bitstrings()
    assert raw[0].join_data().get_counts() == r[0].join_data().get_counts()
    cnt = from_neutral(decode(encode(to_neutral(r, kind="sampler", sampler_storage="counts"))))
    assert cnt[0].data.meas.get_counts() == r[0].data.meas.get_counts()
