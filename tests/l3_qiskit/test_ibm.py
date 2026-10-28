from qiskit_resume.l3_qiskit import ibm


class J:
    _qresume_runtime_like = True

    def __init__(self, status="DONE", msg=None, usage=0):
        self._s, self._m, self._u = status, msg, usage

    def status(self):
        return self._s

    def error_message(self):
        return self._m

    def usage(self):
        return self._u


def test_states_errors_usage():
    assert ibm.job_state(J("INITIALIZING")) == "queued" and ibm.job_state(J("ERROR")) == "failed"
    assert ibm.classify_error(J(msg="Insufficient quota remaining")) == "quota"
    assert ibm.classify_error(J(msg="Backend is under maintenance")) == "device"
    assert ibm.classify_error(J(msg="Circuit is not ISA / instruction not supported")) == "circuit"
    assert ibm.classify_error(J(msg="batch TTL expired")) == "ttl"
    assert ibm.classify_error(J(msg="???")) == "unknown"
    assert ibm.job_usage_s(J(usage=0), wait_s=0) is None and ibm.job_usage_s(J(usage=3)) == 3.0
    assert ibm.is_runtime_job(J()) and not ibm.is_runtime_job(object())
