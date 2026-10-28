import numpy as np

from qiskit_resume.l1_record.hashing import diff_parts, digest_parts, normalize_floats


def test_same_input_same_key_regardless_of_order():
    a = digest_parts({"circuit": b"c", "params": b"p"})
    b = digest_parts({"params": b"p", "circuit": b"c"})
    assert a == b and len(a.key_hash) == 64


def test_one_part_change_changes_key_and_is_reported():
    a = digest_parts({"circuit": b"c", "params": b"p"})
    b = digest_parts({"circuit": b"c", "params": b"q"})
    assert a.key_hash != b.key_hash
    assert diff_parts(a, b) == ["params"]
    assert diff_parts(a, digest_parts({"circuit": b"c"})) == ["params"]


def test_normalize_floats_absorbs_last_bit_noise_but_not_real_changes():
    x = np.array([0.1 + 0.2, -1.1372839182734567, 0.0])
    y = np.array([0.3, -1.1372839182734569, -0.0])
    assert normalize_floats(x) == normalize_floats(y)
    assert normalize_floats([0.3]) != normalize_floats([0.3000000001])
    assert normalize_floats(np.zeros((2, 3))) != normalize_floats(np.zeros((3, 2)))  # shape matters
    assert normalize_floats([float("nan")]) == normalize_floats([float("nan")])
