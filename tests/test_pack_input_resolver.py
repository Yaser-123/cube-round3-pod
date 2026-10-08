import base64
import hashlib

import pytest

from agents.pack.input_resolver import MAX_BASE64_LENGTH, resolve_image


def test_resolve_image_without_data_base64_falls_back_to_local_file(tmp_path, monkeypatch):
    image = b"\x89PNG\r\n\x1a\n"
    (tmp_path / "open_box.png").write_bytes(image)
    monkeypatch.setenv("INPUT_DIR", str(tmp_path))
    expected_sha256 = hashlib.sha256(image).hexdigest().upper()

    resolved_image, mime_type = resolve_image("open_box.png", expected_sha256)

    assert resolved_image == image
    assert mime_type == "image/png"


def test_resolve_image_accepts_valid_base64_data():
    image = b"\x89PNG\r\n\x1a\n"
    expected_sha256 = hashlib.sha256(image).hexdigest().upper()

    resolved_image, mime_type = resolve_image(
        "UNIT-0006/pack/open_box.png",
        expected_sha256,
        base64.b64encode(image).decode("ascii"),
    )

    assert resolved_image == image
    assert mime_type == "image/png"


def test_resolve_image_rejects_base64_sha256_mismatch():
    image = b"\x89PNG\r\n\x1a\n"

    with pytest.raises(ValueError, match="Input hash mismatch"):
        resolve_image(
            "open_box.png",
            "0" * 64,
            base64.b64encode(image).decode("ascii"),
        )


def test_resolve_image_rejects_invalid_base64():
    with pytest.raises(ValueError, match="not valid base64"):
        resolve_image("open_box.png", "0" * 64, "not base64!")


def test_resolve_image_rejects_base64_above_image_size_limit():
    with pytest.raises(ValueError, match="8 MiB|8388608"):
        resolve_image("open_box.png", "0" * 64, "A" * (MAX_BASE64_LENGTH + 1))
