import logging
import os
import signal
import threading
import time
from pathlib import Path

import pytest

from documentscan import daemon
from documentscan.config import Config


class FakeConn:
    def __init__(self):
        self.sent = []
        self.closed = False

    def send(self, obj):
        self.sent.append(obj)

    def close(self):
        self.closed = True


def test_session_reports_not_found(monkeypatch, caplog):
    from documentscan import scanner as scanner_mod

    class NotThere:
        def __init__(self, config):
            pass

        def connect(self):
            raise scanner_mod.ScannerNotFound("no scanner found")

        def close(self):
            pass

    monkeypatch.setattr(scanner_mod, "Scanner", NotThere)
    conn = FakeConn()
    with caplog.at_level(logging.WARNING, logger="documentscan"):
        code = daemon._Session(Config.from_env(), conn).run()
    assert code == daemon.EXIT_NOT_FOUND
    assert conn.sent == []
    assert conn.closed
    assert "no scanner found" in caplog.text


def test_session_reports_connected_then_error(monkeypatch):
    from documentscan import scanner as scanner_mod

    class Flaky:
        def __init__(self, config):
            pass

        def connect(self):
            pass

        def page_loaded(self):
            return False

        def check_alive(self):
            raise OSError("usb went away")

        def close(self):
            pass

    monkeypatch.setattr(scanner_mod, "Scanner", Flaky)
    conn = FakeConn()
    cfg = Config.from_env()
    object.__setattr__(cfg, "poll_interval", 0.01)
    code = daemon._Session(cfg, conn).run()
    assert code == daemon.EXIT_SCANNER_ERROR
    assert conn.sent == ["connected"]


def test_session_handles_real_sane_errors(monkeypatch):
    """python-sane raises _sane.error, which is not re-exported as sane.error."""
    from documentscan import scanner as scanner_mod

    class Broken:
        def __init__(self, config):
            pass

        def connect(self):
            pass

        def page_loaded(self):
            raise scanner_mod.SaneError("Device busy")

        def close(self):
            pass

    monkeypatch.setattr(scanner_mod, "Scanner", Broken)
    code = daemon._Session(Config.from_env(), FakeConn()).run()
    assert code == daemon.EXIT_SCANNER_ERROR


def test_supervisor_spawns_fresh_worker_per_attempt(monkeypatch, tmp_path: Path, caplog):
    """Two failed attempts, then a session that connects and runs until shutdown.

    Every attempt runs in its own process, so file descriptors leaked by one
    attempt must not be visible to the next.
    """
    monkeypatch.setattr(daemon, "BACKOFF_MIN", 0.05)
    monkeypatch.setattr(daemon, "BACKOFF_MAX", 0.1)
    log_dir = tmp_path

    def session(config, conn):
        attempt = len(list(log_dir.glob("attempt-*"))) + 1
        (log_dir / f"attempt-{attempt}").write_text(str(os.getpid()))
        # Leak a descriptor: it must die with this process.
        os.open(os.devnull, os.O_RDONLY)
        if attempt < 3:
            conn.close()
            raise SystemExit(daemon.EXIT_NOT_FOUND)
        stop = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stop.set())
        conn.send("connected")
        (log_dir / "connected").touch()
        stop.wait(10)
        conn.close()
        raise SystemExit(daemon.EXIT_STOPPED)

    d = daemon.Daemon(Config.from_env(), session=session)

    def stop_when_connected():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and not (log_dir / "connected").exists():
            time.sleep(0.05)
        d.stop()

    fds_before = len(os.listdir("/proc/self/fd"))
    threading.Thread(target=stop_when_connected, daemon=True).start()
    with caplog.at_level(logging.INFO, logger="documentscan"):
        rc = d.run()

    assert rc == 0
    assert (log_dir / "connected").exists()
    pids = {(log_dir / f"attempt-{i}").read_text() for i in (1, 2, 3)}
    assert len(pids) == 3, "each attempt must run in a fresh process"
    assert str(os.getpid()) not in pids
    assert caplog.text.count("Retrying in") == 2
    assert len(os.listdir("/proc/self/fd")) == fds_before


@pytest.mark.skipif(not hasattr(signal, "SIGTERM"), reason="POSIX only")
def test_supervisor_terminates_hung_worker(monkeypatch):
    monkeypatch.setattr(daemon, "WORKER_STOP_TIMEOUT", 0.5)

    def session(config, conn):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        conn.send("connected")
        time.sleep(30)

    d = daemon.Daemon(Config.from_env(), session=session)
    threading.Timer(0.5, d.stop).start()
    started = time.monotonic()
    assert d.run() == 0
    assert time.monotonic() - started < 5


def test_scan_streams_pages_into_one_pdf(tmp_path: Path):
    """Pages are processed one by one as the scanner yields them; blank ones are dropped."""
    import numpy as np
    from PIL import Image

    def text_page():
        arr = np.full((160, 120, 3), 230, dtype=np.uint8)
        arr[40:44, 20:100] = 20
        return Image.fromarray(arr)

    consumed = []

    class Feeder:
        def iter_pages(self):
            for page in (text_page(), Image.new("RGB", (120, 160), (230, 230, 230)), text_page()):
                consumed.append(page)
                yield page

    cfg = Config(output_dir=str(tmp_path), deskew=False, autocrop=False)
    daemon._Session(cfg, FakeConn())._scan_and_deliver(Feeder())

    (pdf,) = tmp_path.glob("Scan_*.pdf")
    assert len(consumed) == 3
    assert pdf.read_bytes().count(b"/MediaBox") == 2


def test_scan_without_pages_delivers_nothing(tmp_path: Path):
    class Empty:
        def iter_pages(self):
            return iter(())

    daemon._Session(Config(output_dir=str(tmp_path)), FakeConn())._scan_and_deliver(Empty())
    assert not list(tmp_path.glob("*.pdf"))
