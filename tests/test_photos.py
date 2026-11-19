import io

import pytest
from PIL import ExifTags, Image

from photos import MAX_UPLOAD_BYTES, PhotoError, clean_text, process_image


def gps_exif(orientation=None):
    exif = Image.Exif()
    exif[ExifTags.Base.Make] = "TestPhone"
    exif[ExifTags.IFD.GPSInfo] = {1: "N", 2: (18.0, 5.0, 0.0), 3: "E", 4: (82.0, 40.0, 0.0)}
    if orientation:
        exif[ExifTags.Base.Orientation] = orientation
    return exif.tobytes()


def test_gps_and_camera_data_are_removed(make_image):
    raw = make_image(exif=gps_exif())
    assert Image.open(io.BytesIO(raw)).getexif().get_ifd(ExifTags.IFD.GPSInfo)  # the input really has GPS

    result = process_image(raw)
    stored = Image.open(io.BytesIO(result["full"]))
    assert len(stored.getexif()) == 0
    assert b"TestPhone" not in result["full"]
    assert stored.format == "JPEG"


def test_large_photos_are_resized_and_thumbnailed(make_image):
    result = process_image(make_image(size=(4000, 3000)))
    assert (result["width"], result["height"]) == (1600, 1200)
    assert max(Image.open(io.BytesIO(result["thumb"])).size) == 480


def test_phone_rotation_is_applied(make_image):
    # Orientation 6 = the camera was turned; the picture should come out portrait.
    result = process_image(make_image(size=(1200, 900), exif=gps_exif(orientation=6)))
    assert result["height"] > result["width"]


def test_png_with_transparency_and_webp_are_accepted(make_image):
    assert process_image(make_image(fmt="PNG", mode="RGBA"))["original_format"] == "PNG"
    assert process_image(make_image(fmt="WEBP"))["original_format"] == "WEBP"


@pytest.mark.parametrize(
    "payload, code",
    [
        (b"", "no_file"),
        (b"<html>not an image</html>", "wrong_type"),
        (b"\xff\xd8\xff" + b"0" * 500, "wrong_type"),  # looks like a JPEG header, isn't one
        (b"x" * (MAX_UPLOAD_BYTES + 1), "too_large"),
    ],
)
def test_bad_files_are_refused(payload, code):
    with pytest.raises(PhotoError) as problem:
        process_image(payload)
    assert problem.value.code == code


def test_gif_is_refused(make_image):
    with pytest.raises(PhotoError) as problem:
        process_image(make_image(fmt="GIF", mode="P"))
    assert problem.value.code == "wrong_type"


def test_clean_text():
    assert clean_text("  yellow\n\tspots\x00 on  leaves ", 280) == "yellow spots on leaves"
    assert clean_text("a" * 500, 280) == "a" * 280
    assert clean_text(None, 10) == ""
