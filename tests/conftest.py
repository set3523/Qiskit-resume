import pytest

from qiskit_resume.l0_disk.blobs import BlobStore
from qiskit_resume.l0_disk.db import ExperimentDB
from qiskit_resume.l1_record.journal import Journal


@pytest.fixture
def db(tmp_path):
    d = ExperimentDB.open_for_run(tmp_path / "exp.qresume")
    yield d
    d.close()


@pytest.fixture
def blobs(db):
    return BlobStore(db)


@pytest.fixture
def journal(db, blobs):
    return Journal(db, blobs)
