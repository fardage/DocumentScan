"""Main loop: wait for the scanner button, scan, build a PDF, drop it in the outbox.

The process is split in two:

* a supervisor (the service's main process) that never touches SANE, and
* a worker process per scanner session that initialises SANE, waits for the
  button and scans.

libsane's USB layer leaks a libusb context (an eventfd/timerfd pair) every
time the library is initialised and torn down, and the only way to make it
rescan the USB bus for a scanner that just reappeared is exactly such a
teardown/re-init cycle.  A single long-lived process that retried every 30 s
while the scanner lid was closed therefore ran into its open-file limit after
a few hours and never saw the scanner again.  Running every attempt in a
short-lived worker makes the leak die with the worker.
"""

from __future__ import annotations

import logging
import multiprocessing
import shutil
import signal
import sys
import tempfile
import time
from multiprocessing.connection import Connection
from pathlib import Path

from .config import Config

log = logging.getLogger("documentscan")

BACKOFF_MIN = 2.0
BACKOFF_MAX = 30.0
WORKER_STOP_TIMEOUT = 15.0
SPOOL_PREFIX = "scan-"

# Worker exit codes.
EXIT_STOPPED = 0
EXIT_NOT_FOUND = 3
EXIT_SCANNER_ERROR = 4
EXIT_UNEXPECTED = 5


class Daemon:
    """Supervisor: spawns a worker per scanner session and backs off between attempts."""

    def __init__(self, config: Config, session=None):
        self.config = config
        self.running = True
        self._session = session or run_session
        self._ctx = multiprocessing.get_context("fork")
        self._worker: multiprocessing.process.BaseProcess | None = None

    def stop(self, *_args) -> None:
        log.info("Shutdown requested")
        self.running = False

    # ------------------------------------------------------------------

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info(
            "DocumentScan starting: source=%r mode=%r dpi=%d rotate=%d/%d enhance=%s output_dir=%s",
            self.config.scan_source,
            self.config.scan_mode,
            self.config.scan_resolution,
            self.config.rotate_front,
            self.config.rotate_back,
            self.config.enhance,
            self.config.output_dir,
        )
        clean_spool(self.config.spool_dir)

        backoff = BACKOFF_MIN
        while self.running:
            connected, exitcode = self._run_worker()
            if not self.running:
                break
            if connected:
                backoff = BACKOFF_MIN
            if exitcode == EXIT_NOT_FOUND:
                log.warning("Retrying in %.0fs", backoff)
            else:
                log.warning("Scanner session ended (exit code %s); reconnecting in %.0fs", exitcode, backoff)
            self._sleep(backoff)
            backoff = min(backoff * 2, BACKOFF_MAX)

        log.info("DocumentScan stopped")
        return 0

    def _run_worker(self) -> tuple[bool, int | None]:
        """Run one scanner session in a child process.

        Returns (connected, exitcode): whether the worker reported a successful
        connection before it ended, and its exit code (None if it was killed).
        """
        parent_conn, child_conn = self._ctx.Pipe(duplex=False)
        worker = self._ctx.Process(
            target=self._session,
            args=(self.config, child_conn),
            name="scanner-session",
            daemon=False,
        )
        worker.start()
        child_conn.close()
        self._worker = worker

        connected = False
        try:
            while self.running and worker.is_alive():
                if parent_conn.poll(0.25):
                    try:
                        if parent_conn.recv() == "connected":
                            connected = True
                    except EOFError:
                        break
            if worker.is_alive():
                worker.terminate()
                worker.join(WORKER_STOP_TIMEOUT)
                if worker.is_alive():
                    log.warning("Scanner worker did not stop in time; killing it")
                    worker.kill()
            worker.join()
            # Drain a "connected" message that raced with worker exit.
            while parent_conn.poll():
                try:
                    if parent_conn.recv() == "connected":
                        connected = True
                except EOFError:
                    break
            exitcode = worker.exitcode
        finally:
            parent_conn.close()
            self._worker = None
            if worker.exitcode is not None:
                worker.close()  # release the sentinel fd and other resources
        return connected, exitcode

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while self.running and time.monotonic() < deadline:
            time.sleep(min(0.25, deadline - time.monotonic()))


def clean_spool(spool_dir: str) -> None:
    """Remove raw pages left behind by a scan that was killed midway."""
    for leftover in Path(spool_dir).glob(SPOOL_PREFIX + "*"):
        log.warning("Removing raw pages of an interrupted scan: %s", leftover)
        shutil.rmtree(leftover, ignore_errors=True)


# ----------------------------------------------------------------------
# Worker


def run_session(config: Config, conn: Connection) -> None:
    """Worker entry point: connect to the scanner, serve button presses until it goes away."""
    sys.exit(_Session(config, conn).run())


class _Session:
    def __init__(self, config: Config, conn: Connection):
        self.config = config
        self.conn = conn
        self.running = True

    def stop(self, *_args) -> None:
        self.running = False

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)

        # Imported here so the supervisor process never loads libsane.
        from .scanner import SaneError, Scanner, ScannerNotFound  # noqa: PLC0415

        scanner = Scanner(self.config)
        try:
            scanner.connect()
            self.conn.send("connected")
            self._poll_loop(scanner)
            return EXIT_STOPPED
        except ScannerNotFound as exc:
            log.warning("%s", exc)
            return EXIT_NOT_FOUND
        except (SaneError, OSError) as exc:
            log.warning("Scanner error: %s", exc)
            return EXIT_SCANNER_ERROR
        except Exception:  # noqa: BLE001
            log.exception("Unexpected error in scanner session")
            return EXIT_UNEXPECTED
        finally:
            scanner.close()
            self.conn.close()

    def _poll_loop(self, scanner) -> None:
        log.info("Waiting for the scan button (poll every %.2fs)", self.config.poll_interval)
        paper_was_loaded = scanner.page_loaded()
        while self.running:
            time.sleep(self.config.poll_interval)
            scanner.check_alive()
            pressed = scanner.button_pressed()
            loaded = scanner.page_loaded()

            trigger = None
            if pressed:
                trigger = "button"
            elif self.config.scan_on_paper_insert and loaded and not paper_was_loaded:
                trigger = "paper inserted"
            paper_was_loaded = loaded

            if trigger is None:
                continue

            if not loaded:
                log.info("Scan button pressed but no paper in the feeder; ignoring")
                continue

            log.info("Scan triggered (%s)", trigger)
            self._scan_and_deliver(scanner)
            # Drain a possibly latched button state so we don't scan twice.
            scanner.button_pressed()
            paper_was_loaded = scanner.page_loaded()

    def _scan_and_deliver(self, scanner) -> None:
        from PIL import Image  # noqa: PLC0415

        from .enhance import process_page, select_pages  # noqa: PLC0415
        from .pdf import encode_page, jpegs_to_pdf  # noqa: PLC0415
        from .uploader import deliver  # noqa: PLC0415

        cfg = self.config
        started = time.monotonic()
        # A raw 300 dpi colour side is about 27 MB, more than a Pi Zero can
        # hold for a whole batch, and processing one takes longer than
        # scanning it. Raw pages therefore go to disk as they come off the
        # scanner, so the feeder runs through the stack without pausing, and
        # are processed one by one afterwards; only JPEGs stay in memory.
        with tempfile.TemporaryDirectory(prefix=SPOOL_PREFIX, dir=cfg.spool_dir) as spool:
            raw = []
            for index, image in enumerate(scanner.iter_pages()):
                path = Path(spool) / f"page-{index:03d}.ppm"
                image.save(path)
                del image
                raw.append(path)
            if not raw:
                log.warning("Scan finished with no pages; nothing to deliver")
                return
            log.info("Scanned %d page(s) in %.1fs; processing", len(raw), time.monotonic() - started)
            processed = []
            for index, path in enumerate(raw):
                image = Image.open(path)
                image.load()  # reads the pixels and closes the file
                path.unlink()
                page, kind = process_page(image, index, cfg)
                del image
                processed.append((encode_page(page, cfg.scan_resolution, cfg.jpeg_quality), kind))
                del page
        pages = select_pages(processed, cfg)
        pdf = jpegs_to_pdf(pages)
        path = deliver(pdf, cfg.output_dir, cfg.filename_prefix)
        log.info(
            "Delivered %s (%d pages, %.1f MB) in %.1fs",
            path,
            len(pages),
            len(pdf) / 1e6,
            time.monotonic() - started,
        )


def main() -> int:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    return Daemon(config).run()


if __name__ == "__main__":
    sys.exit(main())
