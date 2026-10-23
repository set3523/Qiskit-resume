import numpy as np
import pytest

from qiskit_resume.l0_disk.codec import EncodeError, decode, encode


def test_codec_roundtrip_exact():
    value = {
        "theta": np.array([0.1, -1.2345678901234567, 3e-300]),
        "counts": {"00": 510, "11": 514},
        "energy": -1.1372839182734567,
        "nan": float("nan"),
        "c": 1 + 2j,
        "t": (1, "a", None, True),
        "scalar": np.float32(1.5),
        "bits": np.array([[1, 0], [0, 1]], dtype=np.uint8),
        "raw": b"\x00\x01",
        "nested": [[1, 2], {"x": np.int64(7)}],
    }
    out = decode(encode(value))
    assert np.array_equal(out["theta"], value["theta"]) and out["theta"].dtype == np.float64
    assert out["theta"].tobytes() == value["theta"].tobytes()  # bit-exact
    assert out["counts"] == value["counts"]
    assert out["energy"] == value["energy"]
    assert out["nan"] != out["nan"]
    assert out["c"] == 1 + 2j
    assert out["t"] == (1, "a", None, True)
    assert out["scalar"].dtype == np.float32 and float(out["scalar"]) == 1.5
    assert out["bits"].shape == (2, 2) and out["bits"].dtype == np.uint8
    assert out["raw"] == b"\x00\x01"
    assert int(out["nested"][1]["x"]) == 7


def test_codec_rejects_unknown_objects_with_path():
    class Opaque:
        pass

    with pytest.raises(EncodeError) as e:
        encode({"opt": {"state": [1, Opaque()]}})
    assert "value.opt.state[1]" in str(e.value)


def test_codec_pickle_opt_in():
    from fractions import Fraction

    assert decode(encode({"f": Fraction(1, 3)}, allow_pickle=True)) == {"f": Fraction(1, 3)}


