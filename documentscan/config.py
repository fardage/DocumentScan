"""Configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    consume_dir: str = "/consume"
    sane_device: str | None = None
    scan_source: str = "ADF Duplex"
    scan_mode: str = "Color"
    scan_resolution: int = 300
    # Slightly wider than A4 so the whole sheet is captured even when it is
    # off-centre or skewed; the backing around it is cropped away afterwards.
    page_width_mm: float = 219.0
    # 0 = let the scanner detect the page length.
    page_height_mm: float = 0.0
    poll_interval: float = 0.5
    puid: int = 1001
    pgid: int = 1001
    filename_prefix: str = "Scan"
    jpeg_quality: int = 85
    log_level: str = "INFO"
    # Scan on button press only, or also when paper is inserted.
    scan_on_paper_insert: bool = False
    # Image processing. ScanSnap feeds pages so they come out upside down.
    rotate_front: int = 180
    rotate_back: int = 180
    enhance: bool = True
    # Percent of darkest/lightest pixels ignored when stretching levels.
    autocontrast_cutoff: float = 1.0
    sharpen: bool = True
    # Straighten the page and crop away the scanner backing around it.
    deskew: bool = True
    autocrop: bool = True
    max_skew_deg: float = 5.0
    crop_inset_mm: float = 1.0

    @classmethod
    def from_env(cls) -> "Config":
        env = os.environ
        return cls(
            consume_dir=env.get("CONSUME_DIR", cls.consume_dir),
            sane_device=env.get("SANE_DEVICE") or None,
            scan_source=env.get("SCAN_SOURCE", cls.scan_source),
            scan_mode=env.get("SCAN_MODE", cls.scan_mode),
            scan_resolution=int(env.get("SCAN_RESOLUTION", cls.scan_resolution)),
            page_width_mm=float(env.get("PAGE_WIDTH_MM", cls.page_width_mm)),
            page_height_mm=float(env.get("PAGE_HEIGHT_MM", cls.page_height_mm)),
            poll_interval=float(env.get("POLL_INTERVAL", cls.poll_interval)),
            puid=int(env.get("PUID", cls.puid)),
            pgid=int(env.get("PGID", cls.pgid)),
            filename_prefix=env.get("FILENAME_PREFIX", cls.filename_prefix),
            jpeg_quality=int(env.get("JPEG_QUALITY", cls.jpeg_quality)),
            log_level=env.get("LOG_LEVEL", cls.log_level).upper(),
            scan_on_paper_insert=_env_bool("SCAN_ON_PAPER_INSERT", cls.scan_on_paper_insert),
            rotate_front=int(env.get("ROTATE_FRONT", cls.rotate_front)),
            rotate_back=int(env.get("ROTATE_BACK", cls.rotate_back)),
            enhance=_env_bool("ENHANCE", cls.enhance),
            autocontrast_cutoff=float(env.get("AUTOCONTRAST_CUTOFF", cls.autocontrast_cutoff)),
            sharpen=_env_bool("SHARPEN", cls.sharpen),
            deskew=_env_bool("DESKEW", cls.deskew),
            autocrop=_env_bool("AUTOCROP", cls.autocrop),
            max_skew_deg=float(env.get("MAX_SKEW_DEG", cls.max_skew_deg)),
            crop_inset_mm=float(env.get("CROP_INSET_MM", cls.crop_inset_mm)),
        )
