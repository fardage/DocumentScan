"""Straighten a scanned page and crop away the scanner backing around it.

The scan window is wider and longer than the sheet, so the scanner backing
(a uniform grey band whose brightness differs per sensor side) shows around
the page. Rather than classifying pixels as paper, which fails for pages with
dark borders or coloured backgrounds, this module walks inward from each image
border and finds, for every column (top/bottom) or row (left/right), the first
pixel that deviates from that line's own backing level. Where the sheet reaches
the image border no straight edge is found and that side is left uncropped.
The four edge lines give the skew angle and, after rotation, the crop box.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

DOWNSAMPLE = 2            # detection works on a half-size luminance image
BACKING_SAMPLES = 2       # outermost pixels used to estimate the backing level
MIN_EDGE_DISTANCE = 4     # edges closer (ds px) to the border are border artefacts
DEVIATION = 30            # luminance change that marks the end of the backing
SEARCH_FRACTION = 0.6     # look this far inward for the edge
MIN_COVERAGE = 0.2        # the edge must span at least this fraction of the side
MIN_SPAN_INLIERS = 0.8    # and be present on this fraction of lines within its span
INLIER_TOL = 3.0          # downsampled pixels
INTERIOR_DEPTH = 40       # pixels (ds) inside the edge that must differ from the backing
MIN_PAGE_FRACTION = 0.25  # crop box must keep at least this much of each dimension


@dataclass
class Edge:
    side: str
    slope: float       # d(position)/d(index), in downsampled pixels
    intercept: float
    inliers: float
    length: float      # span of the edge along the side, in downsampled pixels

    def position_at(self, index: float) -> float:
        return self.slope * index + self.intercept

    def angle_deg(self) -> float:
        """Page rotation (counter-clockwise positive, as PIL.Image.rotate)."""
        a = math.degrees(math.atan(self.slope))
        # top: y grows to the right when the page is rotated clockwise;
        # bottom measured from below and left measured from the left grow
        # with CCW rotation; right measured from the right grows with CW.
        return -a if self.side in ("top", "right") else a


def _luminance(image: Image.Image) -> np.ndarray:
    return np.asarray(image.convert("L").reduce(DOWNSAMPLE), dtype=np.int16)


def _inward_profiles(lum: np.ndarray, side: str) -> np.ndarray:
    """Rows of pixels walking inward from ``side``: shape (lines, depth)."""
    h, w = lum.shape
    if side == "top":
        return lum[: int(h * SEARCH_FRACTION), :].T
    if side == "bottom":
        return lum[::-1][: int(h * SEARCH_FRACTION), :].T
    if side == "left":
        return lum[:, : int(w * SEARCH_FRACTION)]
    return lum[:, ::-1][:, : int(w * SEARCH_FRACTION)]


def _robust_line(x: np.ndarray, y: np.ndarray) -> tuple[float, float, float] | None:
    """Fit y = a*x + b ignoring outliers. Returns (a, b, inlier fraction)."""
    if len(x) < 10:
        return None
    keep = np.ones(len(x), dtype=bool)
    for _ in range(4):
        a, b = np.polyfit(x[keep], y[keep], 1)
        residual = np.abs(y - (a * x + b))
        mad = np.median(residual[keep]) + 0.5
        new_keep = residual < max(INLIER_TOL, 3 * mad)
        if new_keep.sum() < 10:
            break
        keep = new_keep
    a, b = np.polyfit(x[keep], y[keep], 1)
    residual = np.abs(y - (a * x + b))
    return float(a), float(b), float(np.mean(residual < INLIER_TOL))


def find_edge(lum: np.ndarray, side: str) -> Edge | None:
    profiles = _inward_profiles(lum, side)
    n_lines, depth = profiles.shape
    backing = np.median(profiles[:, :BACKING_SAMPLES], axis=1)
    deviates = np.abs(profiles - backing[:, None]) > DEVIATION
    found = deviates.any(axis=1)
    position = np.argmax(deviates, axis=1)

    # A real page edge is followed by page, not by a thin stroke and then more
    # backing (which is what a text line looks like when the sheet itself
    # reaches the image border).
    solid = np.zeros(n_lines, dtype=bool)
    for i in np.flatnonzero(found):
        start = position[i] + 2
        stretch = profiles[i, start:min(start + INTERIOR_DEPTH, depth)]
        if len(stretch) and abs(float(np.median(stretch)) - backing[i]) > DEVIATION:
            solid[i] = True

    index = np.flatnonzero(solid).astype(float)
    if len(index) < 10:
        log.debug("%s: no page edge found", side)
        return None
    fit = _robust_line(index, position[solid].astype(float))
    if fit is None:
        return None
    residual = np.abs(position[solid] - (fit[0] * index + fit[1]))
    inliers = residual < INLIER_TOL
    span = index[inliers].max() - index[inliers].min() + 1 if inliers.any() else 0
    if span < MIN_COVERAGE * n_lines or inliers.sum() / span < MIN_SPAN_INLIERS:
        log.debug("%s: edge candidates are not one contiguous straight line (span %.0f%%, filled %.0f%%)",
                  side, span / n_lines * 100, (inliers.sum() / span * 100) if span else 0)
        return None
    edge = Edge(side, fit[0], fit[1], float(inliers.sum() / span), float(span))
    centre_pos = edge.position_at(n_lines / 2)
    if centre_pos < MIN_EDGE_DISTANCE:
        log.debug("%s: edge at %.1f px (ds) hugs the border; treating as no backing", side, centre_pos)
        return None
    log.debug("%s: edge at %.1f px (ds) angle %.2f deg, inliers %.0f%%, span %.0f%%",
              side, centre_pos, edge.angle_deg(), edge.inliers * 100, span / n_lines * 100)
    return edge


def find_edges(image: Image.Image) -> dict[str, Edge]:
    lum = _luminance(image)
    edges = {}
    for side in ("top", "bottom", "left", "right"):
        edge = find_edge(lum, side)
        if edge is not None:
            edges[side] = edge
    return edges


def _weighted_median(values: list[float], weights: list[float]) -> float:
    order = np.argsort(values)
    v = np.asarray(values)[order]
    w = np.asarray(weights)[order]
    half = w.sum() / 2
    return float(v[np.searchsorted(np.cumsum(w), half)])


def skew_angle(edges: dict[str, Edge], max_angle: float) -> float | None:
    """Skew from the detected edges, longer edges weighing more.

    Sheet-fed scanners let the paper turn slightly while it passes through, so
    the top and bottom edges rarely agree exactly; the long side edges give the
    best overall angle when they are available.
    """
    if not edges:
        return None
    angles = [e.angle_deg() for e in edges.values()]
    angle = _weighted_median(angles, [e.length for e in edges.values()])
    if max(angles) - min(angles) > 1.0:
        log.debug("Edge angles disagree (%s); skipping deskew", [round(a, 2) for a in angles])
        return None
    if abs(angle) > max_angle:
        log.debug("Skew %.2f deg exceeds limit %.1f; skipping deskew", angle, max_angle)
        return None
    return angle


def measure_skew(image: Image.Image, max_angle: float) -> float | None:
    return skew_angle(find_edges(image), max_angle)


def deskew_and_crop(
    image: Image.Image, *, deskew: bool, crop: bool, max_angle: float, inset_px: int
) -> Image.Image:
    edges = find_edges(image)
    if not edges:
        log.info("No scanner backing found around the page; leaving it as is")
        return image

    w, h = image.size
    cx, cy = w / 2, h / 2
    # Edge positions at the image centre line, in full-resolution pixels,
    # measured from their own border.
    centre = {
        side: e.position_at((h if side in ("left", "right") else w) / 2 / DOWNSAMPLE) * DOWNSAMPLE
        for side, e in edges.items()
    }

    angle = skew_angle(edges, max_angle) if deskew else None
    if angle is not None and abs(angle) >= 0.05:
        image = image.rotate(-angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white")
        log.info("Deskewed page by %.2f deg (edges: %s)", angle, ", ".join(sorted(edges)))
    if not crop:
        return image

    # After rotating about the centre, each edge is axis-aligned at the same
    # distance from the centre it had before (cos of a small angle ~ 1).
    nw, nh = image.size
    ncx, ncy = nw / 2, nh / 2
    left = ncx - (cx - centre["left"]) + inset_px if "left" in centre else 0
    right = ncx + (cx - centre["right"]) - inset_px if "right" in centre else nw
    top = ncy - (cy - centre["top"]) + inset_px if "top" in centre else 0
    bottom = ncy + (cy - centre["bottom"]) - inset_px if "bottom" in centre else nh
    box = (int(round(left)), int(round(top)), int(round(right)), int(round(bottom)))
    if box[2] - box[0] < w * MIN_PAGE_FRACTION or box[3] - box[1] < h * MIN_PAGE_FRACTION:
        log.warning("Detected page box %s implausibly small; not cropping", box)
        return image
    image = image.crop(box)
    log.info("Cropped page to %dx%d px (removed %s)", image.width, image.height, ", ".join(sorted(centre)))
    return image
