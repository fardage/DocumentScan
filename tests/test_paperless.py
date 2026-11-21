import threading

import pytest
import requests

from documentscan import paperless
from documentscan.paperless import FAILED_SUBDIR, Client, PaperlessError, Uploader


class FakeClient:
    """Paperless stand-in: tasks start pending and are settled by the test."""

    def __init__(self):
        self.uploads = []
        self.tasks = {}

    def upload(self, pdf):
        task_id = f"task-{len(self.uploads)}"
        self.uploads.append(pdf.name)
        self.tasks[task_id] = None  # not registered yet
        return task_id

    def task(self, task_id):
        return self.tasks[task_id]


def _pdf(outbox, name="Scan_2026-11-21_12-00-00.pdf"):
    path = outbox / name
    path.write_bytes(b"%PDF-1.4 test")
    return path


def test_pdf_is_uploaded_once_and_removed_after_success(tmp_path):
    pdf = _pdf(tmp_path)
    client = FakeClient()
    uploader = Uploader(client, str(tmp_path))

    uploader.sync()
    uploader.sync()  # still pending: no second upload
    assert client.uploads == [pdf.name]
    assert pdf.exists()

    client.tasks["task-0"] = {"status": "STARTED"}
    uploader.sync()
    assert pdf.exists()

    client.tasks["task-0"] = {"status": "SUCCESS", "related_document": "42"}
    uploader.sync()
    assert not pdf.exists()
    assert not any(tmp_path.iterdir())
    assert client.uploads == [pdf.name]


def test_failed_consumption_keeps_the_pdf(tmp_path, caplog):
    pdf = _pdf(tmp_path)
    client = FakeClient()
    uploader = Uploader(client, str(tmp_path))
    uploader.sync()
    client.tasks["task-0"] = {"status": "FAILURE", "result": "It is a duplicate"}
    uploader.sync()

    assert (tmp_path / FAILED_SUBDIR / pdf.name).exists()
    assert not list(tmp_path.glob("*.task"))
    assert "duplicate" in caplog.text
    uploader.sync()
    assert client.uploads == [pdf.name], "failed PDFs must not be uploaded again"


def test_task_id_survives_a_restart(tmp_path):
    pdf = _pdf(tmp_path)
    client = FakeClient()
    Uploader(client, str(tmp_path)).sync()
    client.tasks["task-0"] = {"status": "SUCCESS"}
    Uploader(client, str(tmp_path)).sync()  # a fresh process
    assert client.uploads == [pdf.name]
    assert not pdf.exists()


def test_temp_files_are_ignored(tmp_path):
    (tmp_path / ".documentscan-tmp").mkdir()
    (tmp_path / ".documentscan-tmp" / "Scan_x.pdf.part").write_bytes(b"partial")
    client = FakeClient()
    Uploader(client, str(tmp_path)).sync()
    assert client.uploads == []


def test_run_retries_after_errors(tmp_path, monkeypatch):
    calls = []
    stop = threading.Event()

    class Flaky:
        def sync(self):
            calls.append(1)
            if len(calls) < 3:
                raise requests.ConnectionError("tailnet down")
            stop.set()

    paperless.run(Flaky(), interval=0.01, stop=stop)
    assert len(calls) == 3


# -- HTTP client ----------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.headers = {}
        self.responses = responses
        self.requests = []

    def get(self, url, **kwargs):
        self.requests.append(("GET", url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        self.requests.append(("POST", url, kwargs))
        return self.responses.pop(0)


def test_client_sends_token_and_parses_task_id(tmp_path):
    session = FakeSession([FakeResponse(body="0b2c-uuid")])
    client = Client("https://paperless.example:8401/", "secret", session=session)
    assert client.upload(_pdf(tmp_path)) == "0b2c-uuid"
    method, url, kwargs = session.requests[0]
    assert (method, url) == ("POST", "https://paperless.example:8401/api/documents/post_document/")
    assert session.headers["Authorization"] == "Token secret"
    assert kwargs["files"]["document"][0] == "Scan_2026-11-21_12-00-00.pdf"


@pytest.mark.parametrize("body", [
    [{"task_id": "t", "status": "SUCCESS"}],
    {"count": 1, "results": [{"task_id": "t", "status": "SUCCESS"}]},
])
def test_client_reads_plain_and_paginated_task_lists(body):
    client = Client("https://p", "x", session=FakeSession([FakeResponse(body=body)]))
    assert client.task("t")["status"] == "SUCCESS"


def test_client_task_not_registered_yet():
    client = Client("https://p", "x", session=FakeSession([FakeResponse(body=[])]))
    assert client.task("t") is None


def test_client_check_reports_bad_token():
    client = Client("https://p", "x", session=FakeSession([FakeResponse(status=401)]))
    with pytest.raises(PaperlessError):
        client.check()


def test_token_is_not_in_config_repr(monkeypatch):
    from documentscan.config import Config

    monkeypatch.setenv("PAPERLESS_TOKEN", "supersecret")
    assert "supersecret" not in repr(Config.from_env())
