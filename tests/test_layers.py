"""Enforces docs/ARCHITECTURE.md section 1 and section 4.1: layer order and declared module dependencies.

If you need a new dependency, add it to ALLOWED here AND to the table in ARCHITECTURE.md section 4.1.
"""

import ast
from pathlib import Path

PKG = Path(__file__).resolve().parents[1] / "qiskit_resume"

LAYERS = ["l0_disk", "l1_record", "l2_replay", "l3_qiskit", "l4_api", "tools"]

# module -> modules it may import (same or lower layer). l4/tools may also import anything in l0~l3.
ALLOWED: dict[str, set[str]] = {
    "l0_disk.db": set(),
    "l0_disk.blobs": {"l0_disk.db"},
    "l0_disk.codec": set(),
    "l1_record.hashing": {"l0_disk.codec"},
    "l1_record.journal": {"l0_disk.db", "l0_disk.blobs"},
    "l1_record.snapshots": {"l0_disk.db", "l0_disk.blobs"},
    "l1_record.runs": {"l0_disk.db"},
    "l2_replay.replay": {"l1_record.hashing", "l1_record.journal", "l1_record.snapshots"},
    "l2_replay.divergence": {"l1_record.journal", "l1_record.runs", "l2_replay.replay"},
    "l2_replay.budget": {"l1_record.journal"},
    "l3_qiskit.circuits": {"l0_disk.codec"},
    "l3_qiskit.results": {"l0_disk.codec"},
    "l3_qiskit.ibm": set(),
    "l4_api.run": {"l4_api.rng", "l4_api.snapshot"},
    "l4_api.rng": set(),
    "l4_api.snapshot": {"l4_api.rng"},
    "l4_api.wrap": {"l4_api.primitive", "l4_api.passmanager", "l4_api.function"},
    "l4_api.primitive": {"l4_api.run"},
    "l4_api.passmanager": {"l4_api.run", "l4_api.rng"},
    "l4_api.device": {"l4_api.run"},
    "l4_api.function": {"l4_api.run"},
}
QISKIT_OK = {"l3_qiskit", "l4_api", "tools"}


def _module_of(path: Path) -> str | None:
    rel = path.relative_to(PKG).parts
    if len(rel) >= 3 and rel[0] in LAYERS:
        return f"{rel[0]}.{rel[1]}"
    if len(rel) >= 2 and rel[0] in LAYERS:
        return rel[0]  # layer __init__
    return None


def _imports(path: Path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                yield a.name, node.lineno
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            yield node.module, node.lineno


def test_layer_and_module_dependencies():
    problems = []
    for path in PKG.rglob("*.py"):
        me = _module_of(path)
        if me is None:
            continue
        my_layer = me.split(".")[0]
        for name, line in _imports(path):
            where = f"{path.relative_to(PKG.parent)}:{line} imports {name}"
            top = name.split(".")[0]
            if top in ("qiskit", "qiskit_ibm_runtime", "qiskit_algorithms") and my_layer not in QISKIT_OK:
                problems.append(f"{where} - Qiskit only allowed in l3/l4/tools")
            if not name.startswith("qiskit_resume."):
                continue
            parts = name.split(".")
            if len(parts) < 3 or parts[1] not in LAYERS:
                continue
            target = f"{parts[1]}.{parts[2]}"
            if target == me:
                continue
            if len(parts) > 3:
                problems.append(f"{where} - use the module's public package, not its internals")
            t_layer = parts[1]
            if LAYERS.index(t_layer) > LAYERS.index(my_layer):
                problems.append(f"{where} - imports a higher layer")
                continue
            if my_layer in ("l4_api", "tools") and t_layer not in ("l4_api", "tools"):
                continue  # l4/tools may use any lower-layer module
            if my_layer == "tools":
                continue
            if target not in ALLOWED.get(me, set()):
                problems.append(f"{where} - undeclared dependency (add to ALLOWED + ARCHITECTURE.md)")
    assert not problems, "\n".join(problems)


def test_every_module_has_contract_docstring():
    for init in PKG.rglob("__init__.py"):
        if _module_of(init) is None:
            continue
        doc = ast.get_docstring(ast.parse(init.read_text(encoding="utf-8")))
        assert doc, f"{init} needs a contract docstring"


def test_python_sources_are_ascii_only():
    """No Korean or other non-ASCII text in code: consoles like Windows cp949 must never choke on our output."""
    root = PKG.parent
    bad = []
    for folder in ("qiskit_resume", "tests", "examples"):
        for path in (root / folder).rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if any(ord(ch) > 127 for ch in line):
                    bad.append(f"{path.relative_to(root)}:{n}")
    assert not bad, "non-ASCII characters in: " + ", ".join(bad[:20])
