"""qiskit-resume: run a Qiskit Primitives script so it can be interrupted and resumed.

Rerun the same script after any interruption: finished calls replay from a local .qresume file,
an IBM job that was still running is reconnected (not resubmitted), only new work uses the QPU.

How to add it to an existing script (also for AI assistants):

    import qiskit_resume as resume

    with resume.resume_run("my_run.qresume"):                   # 1. run everything inside this
        backend = resume.backend()                              # 2. device: chosen once, reused on reruns
        pm = resume.pass_manager(backend, optimization_level=1) # 3. instead of generate_preset_pass_manager
        estimator = resume.wrap(EstimatorV2(mode=backend))     # 4. wrap every Estimator / Sampler
        theta0 = resume.rng("init").uniform(-1, 1, n)           # 5. replace unseeded randomness
        ...                                                     #    the rest of the script is unchanged

    Rules
    - Replace np.random.default_rng() (no seed), np.random.rand(), random.random() used for
      algorithm inputs with draws from resume.rng("purpose"); a random int seed becomes
      resume.seed_for("purpose"). resume.rng("x") returns the same generator for the same name,
      so it is safe inside loops.
    - Replace generate_preset_pass_manager(...) with resume.pass_manager(...) (same arguments);
      it works anywhere a pass manager is accepted, e.g. QAOA(..., transpiler=pm).
    - Do not create Estimators/Samplers inside library code you cannot wrap; pass wrapped ones in.
    - Optional: @resume.wrap on a slow, deterministic classical function records its result so
      reruns skip it (help(resume.wrap)).
    - Verify before spending QPU time:  qresume selftest my_script.py -- <script args>
      (crashes and resumes the script at several points with synthetic results; no QPU, no account).

Full guide: https://github.com/set3523/Qiskit-resume (README "Add it to your script", llms.txt)
"""

from typing import Any

from qiskit_resume._version import __version__
from qiskit_resume.l0_disk.codec import EncodeError
from qiskit_resume.l0_disk.db import AlreadyRunningError, IntegrityError
from qiskit_resume.l1_record.runs import ImmutableSettingError
from qiskit_resume.l4_api.primitive import BudgetExceeded, JobWaitTimeout, QuotaExhausted
from qiskit_resume.l4_api.device import backend
from qiskit_resume.l4_api.passmanager import pass_manager
from qiskit_resume.l4_api.rng import NoActiveRunError, rng, seed_for
from qiskit_resume.l4_api.run import BackendChangedStop, DivergenceStop, ResumeRun, get_active_run, resume_run
from qiskit_resume.l4_api.wrap import wrap

__all__ = [
    "AlreadyRunningError",
    "BackendChangedStop",
    "BudgetExceeded",
    "DivergenceStop",
    "EncodeError",
    "ImmutableSettingError",
    "IntegrityError",
    "JobWaitTimeout",
    "NoActiveRunError",
    "QuotaExhausted",
    "ResumeRun",
    "__version__",
    "backend",
    "get_active_run",
    "pass_manager",
    "resume_run",
    "rng",
    "seed",
    "seed_for",
    "wrap",
]


def __getattr__(name: str) -> Any:
    if name == "seed":  # resume.seed -> the active experiment's seed
        from qiskit_resume.l4_api.rng import seed as _seed

        return _seed()
    raise AttributeError(f"module 'qiskit_resume' has no attribute {name!r}")
