"""Turn scanned page images into a single PDF."""

from __future__ import annotations

import io
from typing import Iterable, Sequence

import img2pdf
from PIL import Image


def encode_page(image: Image.Image, dpi: int, jpeg_quality: int = 85) -> bytes:
    """Encode one page as JPEG, tagged with ``dpi`` so it keeps its physical size."""
    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=jpeg_quality, dpi=(dpi, dpi), optimize=True)
    return buf.getvalue()


def jpegs_to_pdf(pages: Sequence[bytes]) -> bytes:
    """Wrap JPEG pages losslessly into one PDF.

    The physical page size is derived from the pixel size and the JPEG's dpi
    so that a 300 dpi A4 scan becomes an A4 PDF page.
    """
    if not pages:
        raise ValueError("no pages to convert")

    layout = img2pdf.get_layout_fun(
        pagesize=None,  # take size from the image dimensions + dpi
        imgsize=None,
        border=None,
        fit=None,
        auto_orient=False,
    )
    return img2pdf.convert(list(pages), layout_fun=layout)


def images_to_pdf(images: Iterable[Image.Image], dpi: int, jpeg_quality: int = 85) -> bytes:
    """Encode each page as JPEG and wrap them into one PDF."""
    return jpegs_to_pdf([encode_page(image, dpi, jpeg_quality) for image in images])
