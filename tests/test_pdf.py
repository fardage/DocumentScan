from PIL import Image

from documentscan.pdf import images_to_pdf


def test_two_pages_become_one_pdf():
    pages = [Image.new("RGB", (100, 150), "white"), Image.new("L", (100, 150), 128)]
    pdf = images_to_pdf(pages, dpi=100)
    assert pdf.startswith(b"%PDF")
    assert pdf.count(b"/Type /Page\n") == 2 or pdf.count(b"/Type /Page") >= 2


def test_page_size_follows_dpi():
    # 200 px at 100 dpi = 2 inch = 144 pt
    pdf = images_to_pdf([Image.new("RGB", (200, 100), "white")], dpi=100)
    assert b"/MediaBox [ 0 0 144" in pdf or b"/MediaBox [0 0 144" in pdf


def test_empty_raises():
    import pytest

    with pytest.raises(ValueError):
        images_to_pdf([], dpi=300)


def test_encoded_pages_become_one_pdf():
    from documentscan.pdf import encode_page, jpegs_to_pdf

    pages = [encode_page(Image.new("RGB", (200, 100), "white"), dpi=100) for _ in range(3)]
    assert all(p.startswith(b"\xff\xd8") for p in pages)
    pdf = jpegs_to_pdf(pages)
    assert pdf.count(b"/MediaBox") == 3
    assert b"/MediaBox [ 0 0 144" in pdf or b"/MediaBox [0 0 144" in pdf
