"""Deliver a finished PDF into the outbox directory."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

TMP_SUBDIR = ".documentscan-tmp"


def make_filename(prefix: str, when: datetime | None = None) -> str:
    when = when or datetime.now()
    return f"{prefix}_{when.strftime('%Y-%m-%d_%H-%M-%S')}.pdf"


def deliver(pdf_bytes: bytes, output_dir: str | os.PathLike, prefix: str) -> Path:
    """Write ``pdf_bytes`` atomically into ``output_dir`` and return the final path.

    The file is first written to a hidden temp directory inside the output
    directory, fsynced, and then renamed into place so whatever picks the
    PDFs up never sees a partially written file.
    """
    outbox = Path(output_dir)
    if not outbox.is_dir():
        raise FileNotFoundError(f"output directory does not exist: {outbox}")

    tmp_dir = outbox / TMP_SUBDIR
    tmp_dir.mkdir(exist_ok=True)

    name = make_filename(prefix)
    final = outbox / name
    counter = 1
    while final.exists():
        final = outbox / f"{name[:-4]}_{counter}.pdf"
        counter += 1

    tmp = tmp_dir / (final.name + ".part")
    with open(tmp, "wb") as fh:
        fh.write(pdf_bytes)
        fh.flush()
        os.fsync(fh.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, final)
    return final
