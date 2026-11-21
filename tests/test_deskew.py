import math

import pytest
from PIL import Image, ImageDraw

from documentscan.config import Config
from documentscan.deskew import deskew_and_crop, find_edges, measure_skew
from documentscan.enhance import process_pages

DPI = 100
PAGE = (int(8.27 * DPI), int(11.69 * DPI))  # A4 at 100 dpi
SCAN = (int(8.62 * DPI), int(12.4 * DPI))    # wider/longer scan window
BACKING = (190, 190, 190)                    # light backing like the S1300 front sensor


def _page(colour=(255, 255, 255), border=None) -> Image.Image:
    page = Image.new("RGB", PAGE, colour)
    draw = ImageDraw.Draw(page)
    if border:
        draw.rectangle([0, 0, PAGE[0] - 1, PAGE[1] - 1], outline=border, width=25)
    for y in range(80, PAGE[1] - 80, 30):
        draw.rectangle([70, y, PAGE[0] - 70, y + 8], fill=(20, 20, 20))
    return page


def _scan(angle_deg: float, page=None, offset=(0, 0), backing=BACKING) -> Image.Image:
    """Synthetic scan: page on backing, rotated, with a dark edge shadow."""
    page = page or _page()
    shadowed = Image.new("RGB", (page.width + 4, page.height + 4), (60, 60, 60))
    shadowed.paste(page, (2, 2))
    canvas = Image.new("RGB", SCAN, backing)
    rotated = shadowed.rotate(angle_deg, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=backing)
    canvas.paste(rotated, ((SCAN[0] - rotated.width) // 2 + offset[0], (SCAN[1] - rotated.height) // 2 + offset[1]))
    return canvas


@pytest.mark.parametrize("angle", [0.0, 1.0, -2.2, 3.5])
def test_measure_skew(angle):
    measured = measure_skew(_scan(angle), max_angle=5.0)
    assert measured is not None
    assert math.isclose(measured, angle, abs_tol=0.2)


def test_all_four_edges_found():
    assert set(find_edges(_scan(1.0))) == {"top", "bottom", "left", "right"}


def test_skew_beyond_limit_is_ignored():
    assert measure_skew(_scan(4.0), max_angle=3.0) is None


def test_no_backing_visible_leaves_image_alone():
    full = _page().resize(SCAN)
    assert find_edges(full) == {}
    out = deskew_and_crop(full, deskew=True, crop=True, max_angle=5.0, inset_px=2)
    assert out.size == SCAN


def _assert_cropped_to_page(out, tolerance=0.015):
    assert abs(out.width - PAGE[0]) < tolerance * PAGE[0], out.size
    assert abs(out.height - PAGE[1]) < tolerance * PAGE[1], out.size


def test_deskew_and_crop_returns_page_without_backing():
    out = deskew_and_crop(_scan(2.0), deskew=True, crop=True, max_angle=5.0, inset_px=4)
    _assert_cropped_to_page(out)
    for corner in [(2, 2), (out.width - 3, 2), (2, out.height - 3), (out.width - 3, out.height - 3)]:
        assert out.getpixel(corner)[0] > 240


def test_dark_page_border_is_kept():
    red = (200, 40, 20)
    out = deskew_and_crop(_scan(1.0, page=_page(border=red)), deskew=True, crop=True, max_angle=5.0, inset_px=4)
    _assert_cropped_to_page(out)
    # the border must survive the crop: corners are red, not backing grey
    for corner in [(2, 2), (out.width - 3, 2), (2, out.height - 3), (out.width - 3, out.height - 3)]:
        r, g, b = out.getpixel(corner)
        assert r > 150 and g < 90, (corner, (r, g, b))


def test_page_touching_one_border_crops_the_others():
    # shift the page so its left edge is off the scan window
    out = deskew_and_crop(_scan(0.0, offset=(-40, 0)), deskew=True, crop=True, max_angle=5.0, inset_px=2)
    assert out.height < SCAN[1] - 20
    assert out.width > PAGE[0] - 45  # left side kept as is


def test_dark_backing_like_back_sensor():
    out = deskew_and_crop(_scan(-1.5, backing=(140, 140, 140)), deskew=True, crop=True, max_angle=5.0, inset_px=4)
    _assert_cropped_to_page(out)


def test_pipeline_end_to_end():
    cfg = Config(scan_resolution=DPI, rotate_front=0, rotate_back=0)
    (out,) = process_pages([_scan(-1.0)], cfg)
    _assert_cropped_to_page(out)
    assert out.convert("L").getpixel((5, 5)) > 240  # cleaned pages may be greyscale


def _s1300_front() -> Image.Image:
    """Like a real S1300 front side: paper only ~27 levels above the backing at
    the right edge (the sensor darkens towards its ends), a dark edge shadow,
    and a leading edge (bottom, after rotation) with little backing below."""
    w, h = SCAN
    canvas = Image.new("RGB", SCAN, (182, 182, 182))
    page_w, page_h = PAGE[0], h - 40 - 12  # 40 rows of backing on top, 12 at the bottom
    left = (w - page_w) // 2
    page = Image.new("RGB", (page_w, page_h))
    # brightness falls from 219 on the left to 209 on the right
    ramp = [int(219 - 10 * x / page_w) for x in range(page_w)]
    row = Image.new("RGB", (page_w, 1))
    row.putdata([(v, v, v) for v in ramp])
    page = row.resize((page_w, page_h))
    draw = ImageDraw.Draw(page)
    for y in range(80, page_h - 80, 30):
        draw.rectangle([70, y, page_w - 70, y + 8], fill=(20, 20, 20))
    shadowed = Image.new("RGB", (page_w + 4, page_h + 4), (140, 140, 140))
    shadowed.paste(page, (2, 2))
    canvas.paste(shadowed, (left - 2, 40 - 2))
    return canvas


def test_low_contrast_front_side_edges_are_found():
    assert set(find_edges(_s1300_front())) == {"top", "bottom", "left", "right"}


def test_side_without_edge_still_loses_the_inset():
    out = deskew_and_crop(_scan(0.0, offset=(-40, 0)), deskew=True, crop=True, max_angle=5.0, inset_px=6)
    uncropped = deskew_and_crop(_scan(0.0, offset=(-40, 0)), deskew=True, crop=True, max_angle=5.0, inset_px=0)
    assert out.width == uncropped.width - 12  # 6 px from each side, found or not
