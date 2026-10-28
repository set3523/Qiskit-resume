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
    python crash_and_resume.py ibm/vqe_small_ibm.py --kill-at-call 6 --kill-delay 20 --qpu-budget 120
    python crash_and_resume.py ibm/vqe_small_ibm.py --set maxiter=300 --estimate     # how many jobs? (no QPU)

What an example file must provide (see standard/*.py):
    SEED                          fixed seed
    build_primitives(seed)        -> {"estimator": ..., "sampler": ..., "pass_manager": ...} (any subset;
                                     an optional "backend" entry names the device for the transpile cache)
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
EXIT_STOPPED = 3  # child stopped cleanly (QPU budget or quota); the record is complete up to that point


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


def child(example: Path, file: Path, params: dict, qpu_budget: float | None) -> None:
    mod = load_example(example)  # imports the libraries first, then qiskit-resume
    import qiskit_resume as resume

    try:
        with resume.resume_run(file, log_calls=True, qpu_budget_s=qpu_budget) as run:
            primitives = mod.build_primitives(mod.SEED)
            backend = primitives.pop("backend", None) or "local"  # optional entry: the device the transpile targets
            primitives = {k: resume.wrap(v, backend=backend) for k, v in primitives.items()}
            result = mod.main(**primitives, **params)
            print(f"this run: {run.stats}", flush=True)
    except (resume.BudgetExceeded, resume.QuotaExhausted) as exc:
        print(f"STOPPED {exc}", flush=True)
        sys.exit(EXIT_STOPPED)
    print("RESULT " + json.dumps(result, sort_keys=True), flush=True)


# ---------------------------------------------------------------------------
# parent: start / kill / restart the child and stream its console output
# ---------------------------------------------------------------------------


def start(example: Path, file: Path, sets: list[str], qpu_budget: float | None = None,
          extra_env: dict | None = None) -> subprocess.Popen:
    cmd = [sys.executable, "-u", str(Path(__file__).resolve()), "--child", str(example), "--file", str(file)]
    if qpu_budget is not None:
        cmd += ["--qpu-budget", str(qpu_budget)]
    for s in sets:
        cmd += ["--set", s]
    env = dict(os.environ, PYTHONIOENCODING="utf-8", **(extra_env or {}))
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env)


def stream(proc: subprocess.Popen, label: str, *, kill_at_call: int | None = None,
           kill_after_s: float | None = None, kill_delay_s: float = 0.0) -> tuple[int | None, bool, dict | None]:
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
    due_at = None
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
        if due and due_at is None:
            due_at = time.monotonic() + kill_delay_s
            if kill_delay_s:
                say(f"\n>>> {label}: kill point reached; killing in {kill_delay_s:g} s "
                    "(lets the job be submitted, so run 2 must reconnect to it)\n")
        if due_at is not None and time.monotonic() >= due_at:
            proc.kill()  # hard kill: no cleanup, no exception handlers, like a crash
            killed = True
            say(f"\n>>> {label}: process KILLED after call #{last_call} (hard kill, like a crash or power cut)\n")
    return proc.wait(), killed, result


def estimate(example: Path, sets: list[str], per_job: list[float]) -> int:
    """Count the quantum calls the workload makes, with synthetic results (no QPU, no queue)."""
    import tempfile

    say("=== estimate: running the workload with synthetic results (no QPU time is used) ===")
    with tempfile.TemporaryDirectory() as tmp:
        f = Path(tmp) / "estimate.qresume"
        proc = start(example, f, sets, extra_env={"QRESUME_MODE": "selftest", "QRESUME_FILE": str(f)})
        counts: dict[str, int] = {}
        for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
            m = re.search(r"call #\d+ (\w+): ", line)
            if m:
                counts[m.group(1)] = counts.get(m.group(1), 0) + 1
            elif not line.startswith("RESULT ") and "[qiskit-resume]" not in line and not line.startswith("  "):
                say(f"[estimate] {line}")
        code = proc.wait()
    if code != 0:
        say(f">>> estimate failed (exit code {code})")
        return code or 1
    jobs = counts.get("estimator", 0) + counts.get("sampler", 0)
    say(f"\nquantum jobs: {jobs}  (" + ", ".join(f"{k} {v}" for k, v in sorted(counts.items())) + ")")
    say("The count assumes the optimizer runs to its iteration limit with made-up results; a real run can stop")
    say("earlier. QPU seconds per job depend on the device, circuit size and shots, so here is the range:")
    for s_ in per_job:
        total = jobs * s_
        flag = "  <-- over the 10-minute Open Plan window" if total > 600 else ""
        say(f"  {s_:>5g} s/job  ->  {total / 60:6.1f} QPU minutes{flag}")
    say("After the first real jobs, the log shows the actual seconds per job ('call #N done: QPU ...s').")
    return 0


def parent(args: argparse.Namespace) -> int:
    example = Path(args.example).resolve()
    if not example.is_file():
        raise SystemExit(f"no such example: {args.example}")
    if args.estimate:
        return estimate(example, args.set, [2.0, 5.0, 10.0])
    if args.file:
        file = Path(args.file)
    elif args.no_kill:
        raise SystemExit("--no-kill needs --file (the record to continue)")
    else:
        file = Path(f"{example.stem}.demo.qresume")
        for p in (file, file.with_name(file.name + ".lock")):  # the demo file always starts fresh
            if p.exists():
                p.unlink()
    kill_after = None if args.kill_at_call is not None else args.kill_after
    if args.no_kill:
        say(f"example : {example}\nrecord  : {file.resolve()}")
        say("\n=== single run, no kill (continues the record if it exists) ===")
        code, _, result = stream(start(example, file, args.set, args.qpu_budget), "run")
        if code == EXIT_STOPPED:
            say(f"\n>>> stopped cleanly at the QPU budget; run the same command again later to continue.")
            return 0
        say(f"\n>>> finished (exit code {code}). result: {result}")
        return code

    say(f"example : {example}")
    say(f"record  : {file.resolve()}")
    say(f"params  : {parse_sets(args.set) or 'example defaults'}")
    when = f"at call #{args.kill_at_call}" if args.kill_at_call is not None else f"{kill_after:g} s after the first call"
    say(f"\n=== run 1: will be killed {when} ===")
    code, killed, result = stream(start(example, file, args.set, args.qpu_budget), "run 1",
                                  kill_at_call=args.kill_at_call, kill_after_s=kill_after,
                                  kill_delay_s=args.kill_delay)
    if not killed:
        say(f"\n>>> run 1 ended by itself (exit code {code}) before the kill point; "
            "run 2 will only replay. Use a smaller --kill-at-call / --kill-after.")

    if code == EXIT_STOPPED:
        say("\n>>> run 1 stopped cleanly at the QPU budget (not a crash).")
    say("\n=== run 2: the same command again (resumes from the record) ===")
    code, _, result = stream(start(example, file, args.set, args.qpu_budget), "run 2")
    if code == EXIT_STOPPED:
        say("\n>>> run 2 stopped cleanly at the QPU budget. Everything so far is recorded; run the same "
            f"later with --file {file} --no-kill to continue.")
        return 0
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
        code, _, ref = stream(start(example, ref_file, args.set, args.qpu_budget), "run 3")
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
    ap.add_argument("--kill-delay", type=float, default=0.0,
                    help="wait this many seconds after the kill point before killing (e.g. to kill while an IBM job "
                         "is queued, so run 2 has to reconnect to it)")
    ap.add_argument("--qpu-budget", type=float, help="QPU seconds allowed per run (stops cleanly before exceeding)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=VALUE",
                    help="example parameter, e.g. --set num_qubits=8 (repeatable)")
    ap.add_argument("--no-kill", action="store_true",
                    help="just one normal run on --file (e.g. to continue after a budget stop)")
    ap.add_argument("--estimate", action="store_true",
                    help="only count the quantum jobs the workload makes (synthetic results, no QPU) and project QPU time")
    ap.add_argument("--compare", action="store_true", help="also do a clean run and check the results match")
    ap.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = ap.parse_args()
    if args.child:
        child(Path(args.example), Path(args.file), parse_sets(args.set), args.qpu_budget)
        return 0
    return parent(args)


if __name__ == "__main__":
    sys.exit(main())
