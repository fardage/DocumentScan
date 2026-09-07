"""Post-processing of scanned pages: rotation, auto-levels, sharpening.

The epjitsu backend returns raw sensor data, and ScanSnap feeds pages so that
every side comes out rotated by 180 degrees. This module does what ScanSnap
Manager would do on a PC before the pages are wrapped into a PDF.
"""

from __future__ import annotations

import logging
from typing import Sequence

from PIL import Image, ImageFilter, ImageOps

from .config import Config

log = logging.getLogger(__name__)

DUPLEX_SOURCE = "ADF Duplex"
DARK_CUTOFF = 0.1  # percent of darkest pixels ignored when finding black


def rotate(image: Image.Image, degrees: int) -> Image.Image:
    degrees %= 360
    if degrees == 0:
        return image
    if degrees == 180:
        return image.transpose(Image.Transpose.ROTATE_180)
    if degrees == 90:
        return image.transpose(Image.Transpose.ROTATE_90)
    if degrees == 270:
        return image.transpose(Image.Transpose.ROTATE_270)
    return image.rotate(degrees, expand=True, fillcolor="white")


def enhance(image: Image.Image, cutoff: float, sharpen: bool) -> Image.Image:
    """Stretch levels so paper becomes white and ink black, then sharpen."""
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    if cutoff > 0:
        # preserve_tone stretches all channels by the same amount so colours do
        # not shift. The light-side cutoff ignores the given percent of the
        # brightest pixels so the paper background maps to pure white; the dark
        # side keeps a small fixed cutoff so pages with little ink still work.
        image = ImageOps.autocontrast(
            image, cutoff=(DARK_CUTOFF, cutoff), preserve_tone=True
        )
    if sharpen:
        image = image.filter(ImageFilter.UnsharpMask(radius=1.5, percent=80, threshold=3))
    return image


def process_pages(images: Sequence[Image.Image], config: Config) -> list[Image.Image]:
    """Apply rotation and enhancement to every page.

    In duplex mode the backend alternates front/back sides, so even indices
    are fronts and odd indices are backs; other sources are all fronts.
    """
    duplex = config.scan_source == DUPLEX_SOURCE
    result: list[Image.Image] = []
    for index, image in enumerate(images):
        is_back = duplex and index % 2 == 1
        degrees = config.rotate_back if is_back else config.rotate_front
        page = rotate(image, degrees)
        if config.enhance:
            page = enhance(page, config.autocontrast_cutoff, config.sharpen)
        result.append(page)
    log.debug(
        "Processed %d pages (rotate front=%d back=%d, enhance=%s, cutoff=%.1f%%, sharpen=%s)",
        len(result), config.rotate_front, config.rotate_back, config.enhance,
        config.autocontrast_cutoff, config.sharpen,
    )
    return result
