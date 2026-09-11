"""Post-processing of scanned pages.

The epjitsu backend hands over raw CIS sensor data and offers no brightness,
contrast or gamma controls: the paper comes out as a noisy, slightly blue
light grey (level 220-235 of 255, noise standard deviation around 8), the
back-side sensor is darker than the front one, and thin paper shows the other
side through. This module does what ScanSnap Manager does on a PC before the
pages are wrapped into a PDF:

1. rotate (ScanSnap feeds pages upside down),
2. deskew and crop to the sheet (see :mod:`deskew`),
3. estimate the paper colour and its noise from the histogram peak,
4. denoise: median on luminance, stronger blur on the colour channels,
5. white-balance on the paper colour and stretch levels so the paper is pure
   white and the ink black; everything within a few noise deviations of the
   paper level becomes white, which also removes bleed-through and banding,
6. classify the page as colour, grey or blank; grey pages are stored as
   greyscale and blank pages are dropped,
7. sharpen last, with a threshold above the remaining noise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Sequence

import numpy as np
from PIL import Image, ImageChops, ImageFilter

from .config import Config
from .deskew import deskew_and_crop

log = logging.getLogger(__name__)

DUPLEX_SOURCE = "ADF Duplex"
PageKind = Literal["color", "gray", "blank"]

LUMA = (0.299, 0.587, 0.114)
PAPER_MIN_LEVEL = 128      # the paper peak is searched above this luminance
PAPER_BAND = 12            # pixels within this distance of the peak are paper
HIST_SMOOTH = 7            # histogram smoothing window, in levels
BLACK_PERCENTILE = 0.1     # darkest percent of pixels mapped to black ...
BLACK_MAX_LEVEL = 90       # ... but only when there is ink that dark
CHROMA_BLUR = 2.0
SHARPEN_MIN_THRESHOLD = 3
INK_LEVEL = 128            # luminance below which a pixel counts as ink
COLOR_CHROMA = 60          # channel spread above which a pixel counts as coloured ...
COLOR_MAX_LEVEL = 220      # ... if it is also darker than this


@dataclass(frozen=True)
class Paper:
    """Colour and noise of the paper as found in a page."""

    color: tuple[float, float, float]
    level: float   # luminance of the paper
    sigma: float   # noise standard deviation on the paper


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


def _median_u8(values: np.ndarray) -> float:
    """Median of 8-bit values via their histogram (much faster than sorting)."""
    counts = np.cumsum(np.bincount(values.ravel(), minlength=256))
    return float(np.searchsorted(counts, counts[-1] / 2))


def estimate_paper(image: Image.Image) -> Paper:
    """Find the paper level as the strongest peak in the bright half of the histogram.

    A percentile cannot be used: with noisy sensors the brightest percent of
    pixels are noise peaks well above the paper, so stretching them to white
    leaves the paper grey. The peak is robust to that. Pixels close to the
    peak give the paper colour per channel and, via the median absolute
    deviation, the noise level. Every other pixel is enough for the estimate
    (subsampled, not averaged, so the noise statistics are unchanged).
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    rgb = np.asarray(image)[::2, ::2]
    lum = np.asarray(image.convert("L"))[::2, ::2]
    hist = np.bincount(lum.ravel(), minlength=256).astype(np.float64)
    smooth = np.convolve(hist, np.ones(HIST_SMOOTH) / HIST_SMOOTH, mode="same")
    if smooth[PAPER_MIN_LEVEL:].sum() == 0:
        # No light pixels at all; treat the page as already white-balanced.
        return Paper((255.0, 255.0, 255.0), 255.0, 0.0)
    peak = int(np.argmax(smooth[PAPER_MIN_LEVEL:])) + PAPER_MIN_LEVEL
    band = np.abs(lum.astype(np.int16) - peak) < PAPER_BAND
    color = tuple(_median_u8(rgb[..., c][band]) for c in range(3))
    # Noise per channel from the bright side of the paper level only: nothing
    # but noise is brighter than the paper, so no band is needed there and the
    # estimate is not truncated. For symmetric noise the one-sided median
    # absolute deviation equals the two-sided one. The noisiest channel
    # decides where the white point goes.
    sigma = 0.0
    for c in range(3):
        channel = rgb[..., c]
        level = int(color[c])
        above = channel[channel >= level]
        if len(above):
            sigma = max(sigma, 1.4826 * _median_u8((above - level).astype(np.uint8)))
    return Paper(color, float(peak), sigma)


def _neighbourhoods(a: np.ndarray, pad_mode: str, **pad_kwargs) -> list[np.ndarray]:
    """The nine 3x3-shifted views of ``a``."""
    h, w = a.shape
    p = np.pad(a, 1, mode=pad_mode, **pad_kwargs)
    return [p[dy:dy + h, dx:dx + w] for dy in range(3) for dx in range(3)]


def median3x3(a: np.ndarray) -> np.ndarray:
    """3x3 median of a 2-D uint8 array using a 19-step sorting network.

    Pillow's MedianFilter sorts every neighbourhood individually and needs
    almost two seconds per page; whole-array min/max operations do the same
    work in a fraction of that.
    """
    v = [view.copy() for view in _neighbourhoods(a, "edge")]

    def cx(i: int, j: int) -> None:
        """Compare-exchange: v[i] becomes the smaller, v[j] the larger."""
        lo = np.minimum(v[i], v[j])
        np.maximum(v[i], v[j], out=v[j])
        v[i] = lo

    for i, j in ((1, 2), (4, 5), (7, 8), (0, 1), (3, 4), (6, 7), (1, 2), (4, 5), (7, 8),
                 (0, 3), (5, 8), (4, 7), (3, 6), (1, 4), (2, 5), (4, 7), (4, 2), (6, 4), (4, 2)):
        cx(i, j)
    return v[4]


def erode3x3(mask: np.ndarray) -> np.ndarray:
    """Binary erosion with a 3x3 window; pixels on the border are eroded."""
    out = np.ones_like(mask, dtype=bool)
    for view in _neighbourhoods(mask, "constant", constant_values=False):
        out &= view
    return out


def denoise(image: Image.Image) -> Image.Image:
    """Median filter on luminance, Gaussian blur on the colour channels.

    Text carries no colour information, so the chroma can be blurred much
    harder than the luminance without softening the letters. This removes the
    coloured speckle that makes noisy scans look grainy.
    """
    if image.mode == "L":
        return Image.fromarray(median3x3(np.asarray(image)))
    y, cb, cr = image.convert("YCbCr").split()
    y = Image.fromarray(median3x3(np.asarray(y)))
    cb = cb.filter(ImageFilter.GaussianBlur(CHROMA_BLUR))
    cr = cr.filter(ImageFilter.GaussianBlur(CHROMA_BLUR))
    return Image.merge("YCbCr", (y, cb, cr)).convert("RGB")


def _lut(gain: float, black: float, white: float) -> list[int]:
    levels = np.arange(256, dtype=np.float64) * gain
    out = (levels - black) / (white - black) * 255.0
    return np.clip(np.rint(out), 0, 255).astype(np.uint8).tolist()


def whiten(image: Image.Image, paper: Paper, white_margin: float) -> Image.Image:
    """White-balance on the paper colour and stretch levels: paper white, ink black.

    Each channel is scaled so the paper becomes neutral, then everything
    brighter than ``white_margin`` noise deviations below the paper level is
    clipped to white. The black point is only moved when the page has ink
    that is really dark, so a page with only light content is not stretched
    into fake contrast.
    """
    if image.mode != "RGB":
        image = image.convert("RGB")
    target = max(paper.color)
    gains = [target / c if c > 0 else 1.0 for c in paper.color]
    lum = np.asarray(image.convert("L"))
    darkest = float(np.percentile(lum, BLACK_PERCENTILE))
    black = darkest if darkest < BLACK_MAX_LEVEL else 0.0
    white = max(target - white_margin * paper.sigma, black + 1.0)
    lut: list[int] = []
    for gain in gains:
        lut += _lut(gain, black, white)
    return image.point(lut)


def classify(image: Image.Image, blank_ink_percent: float, color_percent: float) -> PageKind:
    """Decide whether a (cleaned) page is blank, greyscale or coloured.

    Ink is what survives a 3x3 erosion of the dark pixels, so isolated noise
    specks and faint bleed-through do not count. Colour is any saturated
    pixel that is not almost white.
    """
    lum = np.asarray(image.convert("L"))
    ink = np.count_nonzero(erode3x3(lum < INK_LEVEL))
    if ink / lum.size * 100 < blank_ink_percent:
        return "blank"
    if image.mode == "L":
        return "gray"
    r, g, b = image.convert("RGB").split()
    lightest = ImageChops.lighter(ImageChops.lighter(r, g), b)
    darkest = ImageChops.darker(ImageChops.darker(r, g), b)
    chroma = np.asarray(ImageChops.subtract(lightest, darkest))
    colored = np.count_nonzero((chroma > COLOR_CHROMA) & (lum < COLOR_MAX_LEVEL))
    return "color" if colored / lum.size * 100 > color_percent else "gray"


def sharpen(image: Image.Image, sigma: float = 0.0) -> Image.Image:
    threshold = max(SHARPEN_MIN_THRESHOLD, int(round(2 * sigma)))
    return image.filter(ImageFilter.UnsharpMask(radius=1.5, percent=80, threshold=threshold))


def clean_page(image: Image.Image, config: Config) -> tuple[Image.Image, PageKind, Paper]:
    """Denoise, whiten, classify and sharpen one page."""
    paper = estimate_paper(image)
    if config.denoise:
        image = denoise(image)
    image = whiten(image, paper, config.white_margin)
    kind = classify(image, config.blank_ink_percent, config.color_percent)
    if kind == "gray" and config.auto_color:
        image = image.convert("L")
    if config.sharpen:
        image = sharpen(image, paper.sigma)
    return image, kind, paper


def process_pages(images: Sequence[Image.Image], config: Config) -> list[Image.Image]:
    """Rotate, deskew/crop, clean and classify every page; drop blank ones.

    In duplex mode the backend alternates front/back sides, so even indices
    are fronts and odd indices are backs; other sources are all fronts. If
    every page is blank they are all kept: the user pressed the button, so
    something is better than nothing.
    """
    duplex = config.scan_source == DUPLEX_SOURCE
    inset_px = int(round(config.crop_inset_mm / 25.4 * config.scan_resolution))
    processed: list[tuple[Image.Image, PageKind]] = []
    for index, image in enumerate(images):
        side = "back" if duplex and index % 2 == 1 else "front"
        degrees = config.rotate_back if side == "back" else config.rotate_front
        page = rotate(image, degrees)
        if config.deskew or config.autocrop:
            page = deskew_and_crop(
                page,
                deskew=config.deskew,
                crop=config.autocrop,
                max_angle=config.max_skew_deg,
                inset_px=inset_px,
            )
        if config.enhance:
            page, kind, paper = clean_page(page, config)
            log.info(
                "Page %d (%s): paper rgb=(%.0f, %.0f, %.0f) noise=%.1f -> %s",
                index + 1, side, *paper.color, paper.sigma, kind,
            )
        else:
            kind = classify(page, config.blank_ink_percent, config.color_percent)
            if kind == "gray" and config.auto_color:
                page = page.convert("L")
            log.info("Page %d (%s): %s", index + 1, side, kind)
        processed.append((page, kind))

    kept = [page for page, kind in processed if not (config.drop_blank_pages and kind == "blank")]
    if not kept:
        log.warning("All %d pages look blank; keeping them anyway", len(processed))
        kept = [page for page, _kind in processed]
    elif len(kept) < len(processed):
        log.info("Dropped %d blank page(s)", len(processed) - len(kept))
    log.debug(
        "Processed %d pages (rotate front=%d back=%d, enhance=%s, denoise=%s, white_margin=%.1f, "
        "sharpen=%s, deskew=%s, autocrop=%s, auto_color=%s, drop_blank=%s)",
        len(processed), config.rotate_front, config.rotate_back, config.enhance, config.denoise,
        config.white_margin, config.sharpen, config.deskew, config.autocrop, config.auto_color,
        config.drop_blank_pages,
    )
    return kept
