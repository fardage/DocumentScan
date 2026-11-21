"""Upload finished PDFs from the outbox to paperless-ngx.

Runs as its own service next to the scanner daemon. Every PDF in the outbox is
posted to ``/api/documents/post_document/``; the returned consumption task id
is stored next to it (``<name>.pdf.task``) so a restart does not upload it
twice. The PDF is deleted only once paperless reports the task as successful;
if consumption fails (e.g. a duplicate) it is moved to ``failed/`` and the
reason is logged, so a scan is never lost silently.
"""

from __future__ import annotations

import logging
import signal
import sys
import threading
from pathlib import Path

import requests

from .config import Config

log = logging.getLogger("documentscan.paperless")

TASK_SUFFIX = ".task"
FAILED_SUBDIR = "failed"
REQUEST_TIMEOUT = 120  # seconds; a multi-page colour scan is a few MB
BACKOFF_MAX = 300.0


class PaperlessError(RuntimeError):
    """Paperless answered, but not in a way we can use."""


class Client:
    def __init__(self, url: str, token: str, session: requests.Session | None = None):
        self.url = url.rstrip("/")
        self.session = session or requests.Session()
        self.session.headers.update({"Authorization": f"Token {token}", "Accept": "application/json"})

    def check(self) -> None:
        """Fail early on a wrong URL or token."""
        response = self.session.get(f"{self.url}/api/documents/", params={"page_size": 1}, timeout=30)
        if response.status_code in (401, 403):
            raise PaperlessError(f"paperless rejected the token (HTTP {response.status_code})")
        response.raise_for_status()

    def upload(self, pdf: Path) -> str:
        """Post ``pdf`` for consumption and return the task id."""
        with open(pdf, "rb") as fh:
            response = self.session.post(
                f"{self.url}/api/documents/post_document/",
                files={"document": (pdf.name, fh, "application/pdf")},
                timeout=REQUEST_TIMEOUT,
            )
        response.raise_for_status()
        body = response.json()
        # paperless returns the task id as a bare JSON string.
        task_id = body if isinstance(body, str) else (body.get("task_id") or body.get("id"))
        if not task_id:
            raise PaperlessError(f"unexpected post_document response: {body!r}")
        return str(task_id)

    def task(self, task_id: str) -> dict | None:
        """The consumption task, or None while paperless has not registered it yet."""
        response = self.session.get(f"{self.url}/api/tasks/", params={"task_id": task_id}, timeout=30)
        response.raise_for_status()
        body = response.json()
        tasks = body.get("results", []) if isinstance(body, dict) else body
        return tasks[0] if tasks else None


class Uploader:
    def __init__(self, client: Client, outbox: str):
        self.client = client
        self.outbox = Path(outbox)

    def sync(self) -> None:
        """Upload new PDFs and settle the ones paperless has finished with."""
        for pdf in sorted(self.outbox.glob("*.pdf")):
            task_file = pdf.with_name(pdf.name + TASK_SUFFIX)
            if not task_file.exists():
                task_id = self.client.upload(pdf)
                task_file.write_text(task_id)
                log.info("Uploaded %s (task %s)", pdf.name, task_id)
            self._settle(pdf, task_file)

    def _settle(self, pdf: Path, task_file: Path) -> None:
        task = self.client.task(task_file.read_text().strip())
        status = str((task or {}).get("status", "")).upper()
        if status == "SUCCESS":
            pdf.unlink()
            task_file.unlink()
            log.info("Paperless consumed %s (document %s); removed it from the outbox",
                     pdf.name, task.get("related_document"))
        elif status in ("FAILURE", "REVOKED"):
            failed = self.outbox / FAILED_SUBDIR
            failed.mkdir(exist_ok=True)
            pdf.rename(failed / pdf.name)
            task_file.unlink()
            log.error("Paperless could not consume %s: %s; moved it to %s",
                      pdf.name, task.get("result"), failed)


def run(uploader: Uploader, interval: float, stop: threading.Event) -> None:
    backoff = interval
    while not stop.is_set():
        try:
            uploader.sync()
            if backoff != interval:
                log.info("Paperless reachable again")
            backoff = interval
        except (requests.RequestException, PaperlessError, OSError) as exc:
            log.warning("Paperless sync failed: %s; retrying in %.0fs", exc, backoff)
            stop.wait(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)
            continue
        stop.wait(interval)


def main() -> int:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    if not config.paperless_url or not config.paperless_token:
        log.error("PAPERLESS_URL and PAPERLESS_TOKEN must be set")
        return 2

    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())

    client = Client(config.paperless_url, config.paperless_token)
    try:
        client.check()
        log.info("Connected to paperless at %s; watching %s", config.paperless_url, config.output_dir)
    except (requests.RequestException, PaperlessError) as exc:
        log.warning("Paperless check failed: %s; will keep retrying", exc)
    run(Uploader(client, config.output_dir), config.upload_interval, stop)
    log.info("Uploader stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
