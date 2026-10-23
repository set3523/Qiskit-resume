"""Fake IBM-like primitive/job/service for tests (duck-typed like qiskit-ibm-runtime)."""

import itertools

from qiskit.primitives import BaseEstimatorV2, StatevectorEstimator

_ids = itertools.count()


class FakeJob:
    _qresume_runtime_like = True

    def __init__(self, svc, inner_job, fail=None, state="DONE", usage=2.0):
        self.svc, self._inner, self.fail, self.state, self._usage = svc, inner_job, fail, state, usage
        self.id = f"job-{next(_ids)}"
        svc.jobs[self.id] = self

    def job_id(self):
        return self.id

    def status(self):
        return "ERROR" if self.fail else self.state

    def error_message(self):
        return self.fail

    def usage(self):
        return self._usage

    def result(self, timeout=None):
        if self.fail:
            raise RuntimeError(self.fail)
        if self.state == "QUEUED" and timeout is not None:
            class RuntimeJobTimeoutError(Exception):
                pass
            raise RuntimeJobTimeoutError("timeout")
        return self._inner.result()


class FakeService:
    def __init__(self):
        self.jobs = {}

    def job(self, job_id):
        return self.jobs[job_id]


class FakeIBMEstimator(BaseEstimatorV2):
    """Runs locally but behaves like a runtime primitive. ``plan`` = list of failure messages for next jobs."""

    def __init__(self, svc, plan=None, name="fake_ibm_device"):
        self._service, self._plan, self.submitted = svc, list(plan or []), 0
        self._local = StatevectorEstimator()
        self.name = name

    def run(self, pubs, *, precision=None):
        self.submitted += 1
        fail = self._plan.pop(0) if self._plan else None
        return FakeJob(self._service, self._local.run(pubs, precision=precision), fail=fail)
