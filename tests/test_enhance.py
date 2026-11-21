import numpy as np
from PIL import Image

from documentscan.config import Config
from documentscan.enhance import classify, erode3x3, estimate_paper, median3x3, process_pages, rotate

PAPER = (214, 224, 242)  # blue-tinted grey, as the S1300 back sensor delivers it
SIGMA = 8


def _page_with_mark(colour="red"):
    """White page with a coloured mark in the top-left corner."""
    img = Image.new("RGB", (40, 60), "white")
    for x in range(5):
        for y in range(5):
            img.putpixel((x, y), Image.new("RGB", (1, 1), colour).getpixel((0, 0)))
    return img


def _noisy_paper(w=120, h=160, paper=PAPER, sigma=SIGMA, seed=0) -> np.ndarray:
    rng = np.random.default_rng(seed)
    arr = rng.normal(paper, sigma, size=(h, w, 3))
    return np.clip(arr, 0, 255).astype(np.uint8)


def _text_page(**kwargs) -> Image.Image:
    arr = _noisy_paper(**kwargs)
    for y in (40, 60, 80, 100):
        arr[y:y + 4, 20:100] = 20  # black "text lines"
    return Image.fromarray(arr)


def _blank_page(**kwargs) -> Image.Image:
    arr = _noisy_paper(**kwargs)
    arr[70:74, 10:110] = np.array(PAPER) - 15  # faint bleed-through / fold line
    return Image.fromarray(arr)


def _clean_config(**overrides) -> Config:
    base = dict(rotate_front=0, rotate_back=0, deskew=False, autocrop=False)
    base.update(overrides)
    return Config(**base)


# -- rotation -------------------------------------------------------------


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


# -- paper estimate ---------------------------------------------------------


def test_estimate_paper_finds_colour_and_noise():
    paper = estimate_paper(_text_page())
    assert all(abs(found - real) <= 4 for found, real in zip(paper.color, PAPER))
    assert 4 < paper.sigma < 12


def test_estimate_paper_ignores_bright_noise_peaks():
    # A percentile-based white point would sit far above the paper level.
    paper = estimate_paper(_blank_page())
    assert abs(paper.level - 223) <= 4


# -- cleaning -------------------------------------------------------------


def test_clean_page_whitens_paper_and_keeps_text():
    (out,) = process_pages([_text_page()], _clean_config())
    assert out.mode == "L"  # no colour on the page
    arr = np.asarray(out)
    paper = arr[10:30, :]
    assert (paper == 255).mean() > 0.98
    assert arr[41, 50] < 60


def test_clean_page_removes_bleed_through_and_colour_cast():
    cfg = _clean_config(drop_blank_pages=False)
    (out,) = process_pages([_blank_page()], cfg)
    arr = np.asarray(out.convert("RGB"))
    assert (arr == 255).mean() > 0.98


def test_coloured_content_keeps_page_in_colour():
    arr = np.asarray(_text_page()).copy()
    arr[120:150, 20:60] = (200, 30, 30)
    (out,) = process_pages([Image.fromarray(arr)], _clean_config())
    assert out.mode == "RGB"
    r, g, b = out.getpixel((40, 135))
    assert r > 150 and g < 80 and b < 80
    assert out.getpixel((60, 20)) == (255, 255, 255)


def test_gray_input_is_supported():
    (out,) = process_pages([_text_page().convert("L")], _clean_config())
    assert out.mode == "L"
    arr = np.asarray(out)
    assert (arr[10:30, :] == 255).mean() > 0.98
    assert arr[41, 50] < 60


def test_denoise_and_sharpen_can_be_disabled():
    cfg = _clean_config(denoise=False, sharpen=False)
    (out,) = process_pages([_text_page()], cfg)
    assert (np.asarray(out)[10:30, :] == 255).mean() > 0.9


# -- filters ----------------------------------------------------------------


def test_median3x3_matches_brute_force():
    rng = np.random.default_rng(3)
    a = rng.integers(0, 256, size=(20, 30), dtype=np.uint8)
    out = median3x3(a)
    p = np.pad(a, 1, mode="edge")
    expected = np.median(np.stack([p[dy:dy + 20, dx:dx + 30] for dy in range(3) for dx in range(3)]), axis=0)
    assert np.array_equal(out, expected.astype(np.uint8))


def test_erode3x3_removes_specks_keeps_blocks():
    mask = np.zeros((20, 20), dtype=bool)
    mask[3, 3] = True            # single speck
    mask[10:15, 10:15] = True    # 5x5 block
    out = erode3x3(mask)
    assert not out[3, 3]
    assert out[11:14, 11:14].all() and out.sum() == 9


# -- classification -------------------------------------------------------


def test_classify_kinds():
    assert classify(Image.new("RGB", (50, 50), "white"), 0.02, 0.01) == "blank"
    grey = Image.new("RGB", (50, 50), "white")
    grey.paste((0, 0, 0), (10, 10, 30, 30))
    assert classify(grey, 0.02, 0.01) == "gray"
    colour = grey.copy()
    colour.paste((200, 30, 30), (35, 35, 45, 45))
    assert classify(colour, 0.02, 0.01) == "color"


def test_blank_pages_are_dropped():
    pages = process_pages([_text_page(), _blank_page(), _text_page(seed=1)], _clean_config())
    assert len(pages) == 2


def test_all_blank_pages_are_kept():
    pages = process_pages([_blank_page(), _blank_page(seed=1)], _clean_config())
    assert len(pages) == 2


def test_blank_page_dropping_can_be_disabled():
    cfg = _clean_config(drop_blank_pages=False)
    assert len(process_pages([_text_page(), _blank_page()], cfg)) == 2


def test_auto_color_disabled_keeps_rgb():
    cfg = _clean_config(auto_color=False)
    (out,) = process_pages([_text_page()], cfg)
    assert out.mode == "RGB"


def test_enhance_disabled_is_passthrough():
    cfg = Config(enhance=False, rotate_front=0, rotate_back=0, drop_blank_pages=False, auto_color=False)
    img = Image.new("RGB", (10, 10), (200, 200, 200))
    assert process_pages([img], cfg)[0].getpixel((0, 0)) == (200, 200, 200)


def test_median_in_strips_matches_whole_array(monkeypatch):
    from documentscan import enhance

    arr = np.random.default_rng(3).integers(0, 256, size=(103, 37), dtype=np.uint8)
    whole = enhance._median3x3(arr)
    for rows in (1, 2, 10, 50):
        monkeypatch.setattr(enhance, "MEDIAN_STRIP_ROWS", rows)
        assert np.array_equal(median3x3(arr), whole)


def test_select_pages_works_on_encoded_pages():
    from documentscan.enhance import select_pages

    cfg = _clean_config()
    assert select_pages([(b"a", "gray"), (b"b", "blank"), (b"c", "color")], cfg) == [b"a", b"c"]
    assert select_pages([(b"a", "blank"), (b"b", "blank")], cfg) == [b"a", b"b"]
