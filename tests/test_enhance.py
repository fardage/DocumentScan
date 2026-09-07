from PIL import Image

from documentscan.config import Config
from documentscan.enhance import enhance, process_pages, rotate


def _page_with_mark(colour="red"):
    """White page with a coloured mark in the top-left corner."""
    img = Image.new("RGB", (40, 60), "white")
    for x in range(5):
        for y in range(5):
            img.putpixel((x, y), Image.new("RGB", (1, 1), colour).getpixel((0, 0)))
    return img


def test_rotate_180_moves_mark_to_bottom_right():
    out = rotate(_page_with_mark(), 180)
    assert out.getpixel((39, 59)) == (255, 0, 0)
    assert out.getpixel((0, 0)) == (255, 255, 255)


def test_rotate_90_changes_orientation():
    out = rotate(_page_with_mark(), 90)
    assert out.size == (60, 40)


def test_duplex_rotates_each_side_independently():
    cfg = Config(scan_source="ADF Duplex", rotate_front=180, rotate_back=0, enhance=False)
    front, back = process_pages([_page_with_mark(), _page_with_mark()], cfg)
    assert front.getpixel((39, 59)) == (255, 0, 0)
    assert back.getpixel((0, 0)) == (255, 0, 0)


def test_simplex_treats_all_pages_as_front():
    cfg = Config(scan_source="ADF Front", rotate_front=0, rotate_back=180, enhance=False)
    pages = process_pages([_page_with_mark(), _page_with_mark()], cfg)
    assert all(p.getpixel((0, 0)) == (255, 0, 0) for p in pages)


def test_enhance_whitens_grey_background():
    img = Image.new("RGB", (100, 100), (200, 200, 200))
    for x in range(20, 80):
        img.putpixel((x, 50), (60, 60, 60))
    out = enhance(img, cutoff=1.0, sharpen=False)
    assert out.getpixel((5, 5)) == (255, 255, 255)
    assert out.getpixel((50, 50))[0] < 60


def test_enhance_disabled_is_passthrough():
    cfg = Config(enhance=False, rotate_front=0, rotate_back=0)
    img = Image.new("RGB", (10, 10), (200, 200, 200))
    assert process_pages([img], cfg)[0].getpixel((0, 0)) == (200, 200, 200)
