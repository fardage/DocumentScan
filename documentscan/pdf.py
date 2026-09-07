"""Turn a list of scanned page images into a single PDF."""

from __future__ import annotations

import io
from typing import Iterable

import img2pdf
from PIL import Image


def images_to_pdf(images: Iterable[Image.Image], dpi: int, jpeg_quality: int = 85) -> bytes:
    """Encode each page as JPEG and wrap them losslessly into one PDF.

    The physical page size is derived from the pixel size and ``dpi`` so that
    a 300 dpi A4 scan becomes an A4 PDF page.
    """
    pages: list[bytes] = []
    for image in images:
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        buf = io.BytesIO()
        image.save(buf, format="JPEG", quality=jpeg_quality, dpi=(dpi, dpi), optimize=True)
        pages.append(buf.getvalue())

    if not pages:
        raise ValueError("no pages to convert")

    layout = img2pdf.get_layout_fun(
        pagesize=None,  # take size from the image dimensions + dpi
        imgsize=None,
        border=None,
        fit=None,
        auto_orient=False,
    )
    return img2pdf.convert(pages, layout_fun=layout)
