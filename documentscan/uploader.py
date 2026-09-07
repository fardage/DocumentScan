"""Deliver a finished PDF into the paperless-ngx consume directory."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from pathlib import Path

log = logging.getLogger(__name__)

TMP_SUBDIR = ".documentscan-tmp"


def make_filename(prefix: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"{prefix}_{when.strftime('%Y-%m-%d_%H-%M-%S')}.pdf"


def deliver(pdf_bytes: bytes, consume_dir: str | os.PathLike, prefix: str, uid: int, gid: int) -> Path:
    """Write ``pdf_bytes`` atomically into ``consume_dir`` and return the final path.

    The file is first written to a hidden temp directory inside the consume
    directory (which paperless ignores), fsynced, chowned, and then renamed into
    place so paperless never sees a partially written file.
    """
    consume = Path(consume_dir)
    if not consume.is_dir():
        raise FileNotFoundError(f"consume directory does not exist: {consume}")

    tmp_dir = consume / TMP_SUBDIR
    tmp_dir.mkdir(exist_ok=True)

    name = make_filename(prefix)
    final = consume / name
    counter = 1
    while final.exists():
        final = consume / f"{name[:-4]}_{counter}.pdf"
        counter += 1

    tmp = tmp_dir / (final.name + ".part")
    with open(tmp, "wb") as fh:
        fh.write(pdf_bytes)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o644)
    if (os.getuid(), os.getgid()) != (uid, gid):
        # Only needed when the daemon runs as a different user than paperless
        # (e.g. as root); the container normally runs as the paperless user.
        try:
            os.chown(tmp, uid, gid)
        except PermissionError:
            log.warning("Cannot chown %s to %d:%d; paperless may not be able to read it", tmp, uid, gid)
    os.replace(tmp, final)
    return final
