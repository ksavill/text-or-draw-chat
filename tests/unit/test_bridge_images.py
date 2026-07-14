import base64
from io import BytesIO

from PIL import Image
import pytest

from bridge.images import (
    INK_PALETTE,
    PNG_DATA_URL_PREFIX,
    ImageConversionError,
    data_url_to_radio_bitmap,
    radio_bitmap_to_data_url,
)
from pictochat import decode_visible_image, encode_visible_image


def png_data_url(image: Image.Image) -> str:
    output = BytesIO()
    image.save(output, format="PNG")
    return PNG_DATA_URL_PREFIX + base64.b64encode(output.getvalue()).decode("ascii")


def decode_data_url(data_url: str) -> Image.Image:
    assert data_url.startswith(PNG_DATA_URL_PREFIX)
    raw = base64.b64decode(data_url[len(PNG_DATA_URL_PREFIX):])
    image = Image.open(BytesIO(raw))
    image.load()
    return image.convert("RGBA")


@pytest.mark.parametrize(
    ("lowest_ink_y", "expected_bytes"),
    [(0, 2048), (15, 2048), (16, 4096), (79, 10240)],
)
def test_web_png_is_cropped_to_complete_pictochat_rows(
    lowest_ink_y, expected_bytes,
):
    image = Image.new("RGBA", (228, 80), (0, 0, 0, 0))
    image.putpixel((227, lowest_ink_y), (0, 0, 0, 255))

    bitmap = data_url_to_radio_bitmap(png_data_url(image), x_offset=14)

    assert len(bitmap) == expected_bytes
    visible = decode_visible_image(bitmap, x_offset=14)
    assert visible[lowest_ink_y][227] == 1
    assert sum(map(sum, visible)) == 1


def test_web_to_radio_to_png_preserves_visible_monochrome_pixels_and_offset():
    source = Image.new("RGBA", (228, 80), (0, 0, 0, 0))
    source.putpixel((0, 0), (0, 0, 0, 255))
    source.putpixel((227, 31), (20, 20, 20, 255))
    source.putpixel((50, 20), (255, 255, 255, 255))

    bitmap = data_url_to_radio_bitmap(png_data_url(source), x_offset=28)
    rendered = decode_data_url(radio_bitmap_to_data_url(bitmap, x_offset=28))

    assert len(bitmap) == 4096
    assert rendered.size == (228, 80)
    assert rendered.getpixel((0, 0)) == (0, 0, 0, 255)
    assert rendered.getpixel((227, 31)) == (0, 0, 0, 255)
    assert rendered.getpixel((50, 20)) == (0, 0, 0, 0)
    assert rendered.getpixel((0, 32)) == (0, 0, 0, 0)


def test_radio_palette_colours_are_rendered_and_paper_is_transparent():
    visible = [[0] * 228 for _ in range(16)]
    visible[3][4] = 1
    visible[3][5] = 7
    visible[3][6] = 15

    rendered = decode_data_url(
        radio_bitmap_to_data_url(encode_visible_image(visible), x_offset=0)
    )

    assert rendered.getpixel((3, 3)) == (0, 0, 0, 0)
    assert rendered.getpixel((4, 3)) == (*INK_PALETTE[1], 255)
    assert rendered.getpixel((5, 3)) == (*INK_PALETTE[7], 255)
    assert rendered.getpixel((6, 3)) == (*INK_PALETTE[15], 255)


@pytest.mark.parametrize(
    ("data_url", "message"),
    [
        ("not-a-data-url", "base64 PNG data URL"),
        (PNG_DATA_URL_PREFIX + "%%%", "invalid base64"),
        (
            PNG_DATA_URL_PREFIX + base64.b64encode(b"not a PNG").decode("ascii"),
            "invalid PNG",
        ),
    ],
)
def test_invalid_web_image_encodings_are_rejected(data_url, message):
    with pytest.raises(ImageConversionError, match=message):
        data_url_to_radio_bitmap(data_url)


def test_wrong_dimensions_blank_images_and_oversized_pngs_are_rejected():
    wrong_size = Image.new("RGBA", (227, 80), (0, 0, 0, 255))
    blank = Image.new("RGBA", (228, 80), (0, 0, 0, 0))
    valid = png_data_url(Image.new("RGBA", (228, 80), (0, 0, 0, 255)))

    with pytest.raises(ImageConversionError, match="228x80"):
        data_url_to_radio_bitmap(png_data_url(wrong_size))
    with pytest.raises(ImageConversionError, match="no visible ink"):
        data_url_to_radio_bitmap(png_data_url(blank))
    with pytest.raises(ImageConversionError, match="byte limit"):
        data_url_to_radio_bitmap(valid, max_png_bytes=16)


def test_malformed_radio_bitmap_is_rejected():
    with pytest.raises(ImageConversionError, match="2048"):
        radio_bitmap_to_data_url(bytes(17))
