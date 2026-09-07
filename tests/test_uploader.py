import os
from datetime import datetime

from documentscan.uploader import TMP_SUBDIR, deliver, make_filename


def test_filename_format():
    assert make_filename("Scan", datetime(2026, 9, 7, 14, 5, 33)) == "Scan_2026-09-07_14-05-33.pdf"


def test_deliver_writes_atomically(tmp_path):
    path = deliver(b"%PDF-1.4 test", tmp_path, "Scan", os.getuid(), os.getgid())
    assert path.parent == tmp_path
    assert path.read_bytes() == b"%PDF-1.4 test"
    assert oct(path.stat().st_mode & 0o777) == "0o644"
    assert not any((tmp_path / TMP_SUBDIR).iterdir())


def test_deliver_never_overwrites(tmp_path):
    a = deliver(b"a", tmp_path, "Scan", os.getuid(), os.getgid())
    b = deliver(b"b", tmp_path, "Scan", os.getuid(), os.getgid())
    assert a != b
    assert a.read_bytes() == b"a" and b.read_bytes() == b"b"


def test_missing_consume_dir(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        deliver(b"x", tmp_path / "nope", "Scan", 0, 0)
