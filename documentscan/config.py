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
    # Paper whitening, denoising, sharpening and page classification.
    enhance: bool = True
    denoise: bool = True
    # Pixels brighter than the paper level minus this many noise standard
    # deviations become pure white. Raise if backgrounds keep specks or show
    # the other side, lower if very light print disappears.
    white_margin: float = 3.0
    sharpen: bool = True
    # Store pages without coloured content as greyscale.
    auto_color: bool = True
    # Drop pages with less ink than this percent of their area (after the
    # noise has been removed). A few characters are about 0.02 percent.
    drop_blank_pages: bool = True
    blank_ink_percent: float = 0.02
    # A page counts as coloured if more than this percent of its pixels are
    # clearly saturated.
    color_percent: float = 0.01
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
            denoise=_env_bool("DENOISE", cls.denoise),
            white_margin=float(env.get("WHITE_MARGIN", cls.white_margin)),
            sharpen=_env_bool("SHARPEN", cls.sharpen),
            auto_color=_env_bool("AUTO_COLOR", cls.auto_color),
            drop_blank_pages=_env_bool("DROP_BLANK_PAGES", cls.drop_blank_pages),
            blank_ink_percent=float(env.get("BLANK_INK_PERCENT", cls.blank_ink_percent)),
            color_percent=float(env.get("COLOR_PERCENT", cls.color_percent)),
            deskew=_env_bool("DESKEW", cls.deskew),
            autocrop=_env_bool("AUTOCROP", cls.autocrop),
            max_skew_deg=float(env.get("MAX_SKEW_DEG", cls.max_skew_deg)),
            crop_inset_mm=float(env.get("CROP_INSET_MM", cls.crop_inset_mm)),
        )
