"""Main loop: wait for the scanner button, scan, build a PDF, drop it in paperless."""

from __future__ import annotations

import logging
import signal
import sys
import time

import sane

from .config import Config
from .pdf import images_to_pdf
from .scanner import Scanner, ScannerNotFound
from .uploader import deliver

log = logging.getLogger("documentscan")

BACKOFF_MIN = 2.0
BACKOFF_MAX = 30.0


class Daemon:
    def __init__(self, config: Config):
        self.config = config
        self.running = True
        self.scanner = Scanner(config)

    def stop(self, *_args) -> None:
        log.info("Shutdown requested")
        self.running = False

    # ------------------------------------------------------------------

    def run(self) -> int:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        log.info(
            "DocumentScan starting: source=%r mode=%r dpi=%d consume_dir=%s",
            self.config.scan_source,
            self.config.scan_mode,
            self.config.scan_resolution,
            self.config.consume_dir,
        )

        backoff = BACKOFF_MIN
        while self.running:
            try:
                self.scanner.connect()
                backoff = BACKOFF_MIN
                self._poll_loop()
            except ScannerNotFound as exc:
                log.warning("%s; retrying in %.0fs", exc, backoff)
            except (sane.error, OSError) as exc:
                log.warning("Scanner error: %s; reconnecting in %.0fs", exc, backoff)
            except Exception:  # noqa: BLE001
                log.exception("Unexpected error; reconnecting in %.0fs", backoff)
            finally:
                self.scanner.close()

            if self.running:
                self._sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)

        log.info("DocumentScan stopped")
        return 0

    def _sleep(self, seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while self.running and time.monotonic() < deadline:
            time.sleep(min(0.25, deadline - time.monotonic()))

    # ------------------------------------------------------------------

    def _poll_loop(self) -> None:
        log.info("Waiting for the scan button (poll every %.2fs)", self.config.poll_interval)
        paper_was_loaded = self.scanner.page_loaded()
        while self.running:
            time.sleep(self.config.poll_interval)
            pressed = self.scanner.button_pressed()
            loaded = self.scanner.page_loaded()

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
            self._scan_and_deliver()
            # Drain a possibly latched button state so we don't scan twice.
            self.scanner.button_pressed()
            paper_was_loaded = self.scanner.page_loaded()

    def _scan_and_deliver(self) -> None:
        started = time.monotonic()
        pages = self.scanner.scan_pages()
        if not pages:
            log.warning("Scan finished with no pages; nothing to upload")
            return
        pdf = images_to_pdf(pages, self.config.scan_resolution, self.config.jpeg_quality)
        path = deliver(
            pdf,
            self.config.consume_dir,
            self.config.filename_prefix,
            self.config.puid,
            self.config.pgid,
        )
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
