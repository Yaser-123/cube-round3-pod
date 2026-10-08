import hashlib

from agents.pack.input_resolver import resolve_image


def test_resolve_image_accepts_uppercase_expected_sha256(tmp_path, monkeypatch):
    image = b"\x89PNG\r\n\x1a\n"
    (tmp_path / "open_box.png").write_bytes(image)
    monkeypatch.setenv("INPUT_DIR", str(tmp_path))
    expected_sha256 = hashlib.sha256(image).hexdigest().upper()

    resolved_image, mime_type = resolve_image("open_box.png", expected_sha256)

    assert resolved_image == image
    assert mime_type == "image/png"
