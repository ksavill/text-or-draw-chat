"""PNG/data-URL conversion at the web-to-PictoChat boundary."""

from __future__ import annotations

import base64
import binascii
from io import BytesIO

from PIL import Image, UnidentifiedImageError

from pictochat import (
    VISIBLE_HEIGHT,
    VISIBLE_WIDTH,
    CodecError,
    decode_visible_image,
    encode_visible_image,
)


PNG_DATA_URL_PREFIX = "data:image/png;base64,"

# Capture-derived DSi ink palette. Index zero is the transparent paper colour.
INK_PALETTE = (
    (255, 255, 255), (0, 0, 0), (211, 203, 195), (235, 0, 235),
    (251, 0, 138), (251, 0, 40), (251, 73, 0), (251, 162, 0),
    (227, 243, 0), (130, 251, 0), (16, 251, 32), (0, 251, 186),
    (0, 195, 251), (0, 121, 251), (0, 48, 251), (40, 0, 251),
)


class ImageConversionError(ValueError):
    """Raised when a web or radio image cannot be converted safely."""


def _decode_png_data_url(data_url: str, *, max_png_bytes: int) -> Image.Image:
    if not isinstance(data_url, str) or not data_url.startswith(PNG_DATA_URL_PREFIX):
        raise ImageConversionError("drawing must be a base64 PNG data URL")
    encoded = data_url[len(PNG_DATA_URL_PREFIX):]
    # Reject oversized input before allocating the decoded buffer.
    if len(encoded) > ((max_png_bytes + 2) // 3) * 4 + 4:
        raise ImageConversionError("PNG exceeds the configured byte limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageConversionError("drawing contains invalid base64") from exc
    if len(raw) > max_png_bytes:
        raise ImageConversionError("PNG exceeds the configured byte limit")
    try:
        image = Image.open(BytesIO(raw))
        if image.format != "PNG":
            raise ImageConversionError("drawing data is not a PNG")
        # Check dimensions before asking Pillow to decompress the pixel data.
        # A tiny, highly compressed PNG can otherwise allocate far more than
        # the encoded-byte limit is intended to permit.
        if image.size != (VISIBLE_WIDTH, VISIBLE_HEIGHT):
            raise ImageConversionError(
                f"drawing must be {VISIBLE_WIDTH}x{VISIBLE_HEIGHT} pixels"
            )
        image.load()
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as exc:
        raise ImageConversionError("drawing contains an invalid PNG") from exc
    return image.convert("RGBA")


def data_url_to_radio_bitmap(
    data_url: str, *, x_offset: int = 0, ink_threshold: int = 160,
    alpha_threshold: int = 32, max_png_bytes: int = 128 * 1024,
) -> bytes:
    """Convert the browser's 228x80 PNG into cropped PictoChat tile bytes."""

    image = _decode_png_data_url(data_url, max_png_bytes=max_png_bytes)
    source = image.load()
    visible = [[0] * VISIBLE_WIDTH for _ in range(VISIBLE_HEIGHT)]
    lowest_ink_y = -1
    for y in range(VISIBLE_HEIGHT):
        for x in range(VISIBLE_WIDTH):
            red, green, blue, alpha = source[x, y]
            if alpha <= alpha_threshold:
                continue
            # Integer Rec. 601 luma; the web client currently draws pure black.
            luma = (299 * red + 587 * green + 114 * blue) // 1000
            if luma < ink_threshold:
                visible[y][x] = 1
                lowest_ink_y = y
    if lowest_ink_y < 0:
        raise ImageConversionError("drawing contains no visible ink")
    height = min(VISIBLE_HEIGHT, ((lowest_ink_y // 16) + 1) * 16)
    try:
        return encode_visible_image(visible[:height], x_offset=x_offset)
    except CodecError as exc:
        raise ImageConversionError(str(exc)) from exc


def radio_bitmap_to_data_url(bitmap: bytes, *, x_offset: int = 0) -> str:
    """Decode a 1-5 row PictoChat bitmap into a 228x80 transparent PNG."""

    try:
        visible = decode_visible_image(bitmap, x_offset=x_offset)
    except CodecError as exc:
        raise ImageConversionError(str(exc)) from exc
    image = Image.new("RGBA", (VISIBLE_WIDTH, VISIBLE_HEIGHT), (0, 0, 0, 0))
    target = image.load()
    for y, row in enumerate(visible):
        for x, index in enumerate(row):
            if index:
                red, green, blue = INK_PALETTE[index]
                target[x, y] = (red, green, blue, 255)
    output = BytesIO()
    image.save(output, format="PNG", optimize=False)
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return PNG_DATA_URL_PREFIX + encoded
