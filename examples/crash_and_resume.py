"""Run a plain Qiskit example under qiskit-resume, kill it half way, then run it again.

The example files (standard/*.py) are ordinary Qiskit code with no qiskit-resume inside.
This script loads one, wraps its primitives with qiskit-resume, and then:

    run 1   starts the example and hard-kills the process part way (like a crash or power cut)
    run 2   runs the exact same thing again: recorded calls replay from the file, the rest runs
    (--compare) run 3: a clean, uninterrupted run in a separate file; the results must match

Every quantum call is logged to the console, so you can watch where run 1 dies and where run 2
switches from replaying to computing.

    python crash_and_resume.py standard/vqe_ising_spsa.py
    python crash_and_resume.py standard/qaoa_maxcut.py --kill-at-call 40
    python crash_and_resume.py standard/qaoa_maxcut.py --set num_nodes=8 --set reps=2 --kill-at-call 12 --compare

What an example file must provide (see standard/*.py):
    SEED                          fixed seed
    build_primitives(seed)        -> {"estimator": ..., "sampler": ..., "pass_manager": ...} (any subset)
    main(**primitives, **params)  -> dict of results (JSON-serializable)
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

CALL_RE = re.compile(r"call #(\d+) ")


def say(text: str) -> None:
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        enc = sys.stdout.encoding or "ascii"
        print(text.encode(enc, errors="replace").decode(enc, errors="replace"), flush=True)


def parse_sets(items: list[str]) -> dict:
    params = {}
    for item in items:
        key, _, raw = item.partition("=")
        if not key or not _:
            raise SystemExit(f"--set expects key=value, got {item!r}")
        for conv in (int, float):
            try:
                params[key] = conv(raw)
                break
            except ValueError:
                pass
        else:
            params[key] = raw
    return params


# ---------------------------------------------------------------------------
# child: the actual protected run (one process per run)
# ---------------------------------------------------------------------------


def load_example(path: Path):
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for name in ("SEED", "build_primitives", "main"):
        if not hasattr(mod, name):
            raise SystemExit(f"{path.name} does not define {name} (see the docstring of crash_and_resume.py)")
    return mod


def child(example: Path, file: Path, params: dict) -> None:
    mod = load_example(example)  # imports the libraries first, then qiskit-resume
    import qiskit_resume as resume

    with resume.resume_run(file, log_calls=True) as run:
        primitives = {k: resume.wrap(v, backend="local") for k, v in mod.build_primitives(mod.SEED).items()}
        result = mod.main(**primitives, **params)
        print(f"this run: {run.stats}", flush=True)
    print("RESULT " + json.dumps(result, sort_keys=True), flush=True)


# ---------------------------------------------------------------------------
# parent: start / kill / restart the child and stream its console output
# ---------------------------------------------------------------------------


def start(example: Path, file: Path, sets: list[str]) -> subprocess.Popen:
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), "--child", str(example), "--file", str(file)]
    for s in sets:
        cmd += ["--set", s]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)


def stream(proc: subprocess.Popen, label: str, *, kill_at_call: int | None = None,
           kill_after_s: float | None = None) -> tuple[int | None, bool, dict | None]:
    """Print the child's output with a prefix; kill it when the condition is met.

    Returns (exit code, killed, result).
    """
    lines: queue.Queue = queue.Queue()

    def reader() -> None:
        for raw in proc.stdout:
            lines.put(raw.decode("utf-8", errors="replace").rstrip("\r\n"))
        lines.put(None)

    threading.Thread(target=reader, daemon=True).start()
    first_call_at = None
    last_call = 0
    result = None
    killed = False
    while True:
        try:
            line = lines.get(timeout=0.1)
        except queue.Empty:
            line = ""
        if line is None:
            break
        if line:
            if line.startswith("RESULT "):
                result = json.loads(line[len("RESULT "):])
                continue
            say(f"[{label}] {line}")
            m = CALL_RE.search(line)
            if m:
                last_call = int(m.group(1))
                if first_call_at is None:
                    first_call_at = time.monotonic()
        if killed:
            continue
        due = (kill_at_call is not None and last_call >= kill_at_call) or (
            kill_after_s is not None and first_call_at is not None
            and time.monotonic() - first_call_at >= kill_after_s)
        if due:
            proc.kill()  # hard kill: no cleanup, no exception handlers, like a crash
            killed = True
            say(f"\n>>> {label}: process KILLED after call #{last_call} (hard kill, like a crash or power cut)\n")
    return proc.wait(), killed, result


def parent(args: argparse.Namespace) -> int:
    example = Path(args.example).resolve()
    if not example.is_file():
        raise SystemExit(f"no such example: {args.example}")
    if args.file:
        file = Path(args.file)
    else:
        file = Path(f"{example.stem}.demo.qresume")
        for p in (file, file.with_name(file.name + ".lock")):  # the demo file always starts fresh
            if p.exists():
                p.unlink()
    kill_after = None if args.kill_at_call is not None else args.kill_after

    say(f"example : {example}")
    say(f"record  : {file.resolve()}")
    say(f"params  : {parse_sets(args.set) or 'example defaults'}")
    when = f"at call #{args.kill_at_call}" if args.kill_at_call is not None else f"{kill_after:g} s after the first call"
    say(f"\n=== run 1: will be killed {when} ===")
    code, killed, result = stream(start(example, file, args.set), "run 1",
                                  kill_at_call=args.kill_at_call, kill_after_s=kill_after)
    if not killed:
        say(f"\n>>> run 1 ended by itself (exit code {code}) before the kill point; "
            "run 2 will only replay. Use a smaller --kill-at-call / --kill-after.")

    say("\n=== run 2: the same command again (resumes from the record) ===")
    code, _, result = stream(start(example, file, args.set), "run 2")
    if code != 0:
        say(f"\n>>> run 2 failed (exit code {code})")
        return code or 1
    say(f"\n>>> run 2 finished. result: {result}")

    if args.compare:
        ref_file = file.with_name(file.stem + ".reference.qresume")
        for p in (ref_file, ref_file.with_name(ref_file.name + ".lock")):
            if p.exists():
                p.unlink()
        say("\n=== run 3 (--compare): clean run without interruption, separate record ===")
        code, _, ref = stream(start(example, ref_file, args.set), "run 3")
        if code != 0:
            say(f"\n>>> reference run failed (exit code {code})")
            return code or 1
        if ref == result:
            say(f"\n>>> SAME RESULT: interrupted+resumed == uninterrupted  ({result})")
        else:
            say(f"\n>>> DIFFERENT RESULT\n    resumed      : {result}\n    uninterrupted: {ref}")
            return 2
    return 0


def main() -> int:
    for stream_ in (sys.stdout, sys.stderr):
        try:
            stream_.reconfigure(errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("example", help="path to a plain Qiskit example, e.g. standard/qaoa_maxcut.py")
    ap.add_argument("--file", help="experiment record to use (default: <example>.demo.qresume, recreated each time)")
    ap.add_argument("--kill-at-call", type=int, help="kill run 1 once this call number is reached")
    ap.add_argument("--kill-after", type=float, default=10.0,
                    help="kill run 1 this many seconds after its first quantum call (default 10)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="example parameter, e.g. --set num_qubits=8 (repeatable)")
    ap.add_argument("--compare", action="store_true", help="also do a clean run and check the results match")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        child(Path(args.example), Path(args.file), parse_sets(args.set))
        return 0
    return parent(args)


if __name__ == "__main__":
    sys.exit(main())
