"""Seeds and random streams (D-006). See docs/CONTRACTS.md section 5.2.

Holds the active run's seed in a module context set by l4_api.run (no import of run).
"""

from __future__ import annotations

import random as _random
import sys
from typing import Any

import numpy as np

from qiskit_resume.l0_disk.codec import decode, encode


class NoActiveRunError(RuntimeError):
    def __init__(self, what: str = "this") -> None:
        super().__init__(f"{what} needs an active experiment: use it inside `with resume_run(path):`")


_ctx: dict[str, Any] = {"seed": None, "counter": 0, "gens": [], "pending": []}


def activate(seed: int) -> None:
    _ctx.update(seed=int(seed), counter=0, gens=[], pending=[])


def deactivate() -> None:
    _ctx.update(seed=None, counter=0, gens=[], pending=[])


def seed() -> int:
    if _ctx["seed"] is None:
        raise NoActiveRunError("resume.seed")
    return _ctx["seed"]


def rng() -> np.random.Generator:
    """A numpy Generator whose stream is reproduced exactly on replay (n-th call -> n-th stream)."""
    s = seed()
    g = np.random.default_rng([s, _ctx["counter"]])
    _ctx["counter"] += 1
    if _ctx["pending"]:
        g.bit_generator.state = _ctx["pending"].pop(0)
    _ctx["gens"].append(g)
    return g


def apply_global_seed(s: int) -> None:
    _random.seed(s)
    np.random.seed(s % (2**32))
    # Seed qiskit-algorithms only if the script already imported it: importing it here would add
    # seconds of start-up time (it pulls in scipy) for every user who does not need it.
    mod = sys.modules.get("qiskit_algorithms.utils") or sys.modules.get("qiskit_algorithms")
    if mod is not None:
        try:
            from qiskit_algorithms.utils import algorithm_globals

            algorithm_globals.random_seed = s
        except Exception:  # noqa: BLE001
            pass


def capture_state() -> bytes:
    return encode({
        "py": repr(_random.getstate()),
        "np": [str(x) if i == 0 else x for i, x in enumerate(np.random.get_state())],
        "counter": _ctx["counter"],
        "gens": [repr(g.bit_generator.state) for g in _ctx["gens"]],
    })


def restore_state(data: bytes) -> None:
    import ast

    st = decode(data)
    _random.setstate(_tuple(ast.literal_eval(st["py"])))
    np_state = st["np"]
    np.random.set_state((np_state[0], np.asarray(np_state[1], dtype=np.uint32), int(np_state[2]),
                         int(np_state[3]), float(np_state[4])))
    states = [ast.literal_eval(s) for s in st["gens"]]
    for g, s in zip(_ctx["gens"], states):
        g.bit_generator.state = s
    _ctx["pending"] = states[len(_ctx["gens"]):]
    if len(_ctx["gens"]) <= len(states):
        # continue exactly where the captured run was: pending streams are re-created next
        _ctx["counter"] = int(st["counter"]) - len(_ctx["pending"])


def _tuple(x: Any) -> Any:
    return tuple(_tuple(v) for v in x) if isinstance(x, (list, tuple)) else x
