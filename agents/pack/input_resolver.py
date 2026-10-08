"""Resolve and verify Round 3 content-addressed Pack inputs."""
from __future__ import annotations

import base64
import binascii
import hashlib
import mimetypes
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = ROOT / "data" / "input"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_BASE64_LENGTH = 4 * ((MAX_IMAGE_BYTES + 2) // 3)

SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


def _validated_image(image: bytes, input_ref: str, expected_sha256: str) -> tuple[bytes, str]:
    if len(image) > MAX_IMAGE_BYTES:
        raise ValueError(f"Pack image exceeds the {MAX_IMAGE_BYTES}-byte limit.")

    actual_sha256 = hashlib.sha256(image).hexdigest()
    if actual_sha256.lower() != expected_sha256.lower():
        raise ValueError(f"Input hash mismatch for {input_ref}")

    mime_type, _ = mimetypes.guess_type(Path(input_ref).name)
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise ValueError(f"Unsupported Pack image type: {mime_type or 'unknown'}")

    return image, mime_type


def resolve_image(
    input_ref: str,
    expected_sha256: str,
    data_base64: str | None = None,
) -> tuple[bytes, str]:
    if data_base64 is not None:
        if not isinstance(data_base64, str):
            raise ValueError("Pack image data must be a base64 string.")
        if len(data_base64) > MAX_BASE64_LENGTH:
            raise ValueError(f"Pack image exceeds the {MAX_IMAGE_BYTES}-byte limit.")

        try:
            image = base64.b64decode(data_base64.encode("ascii"), validate=True)
        except (binascii.Error, UnicodeEncodeError, ValueError) as exc:
            raise ValueError("Pack image data is not valid base64.") from exc

        return _validated_image(image, input_ref, expected_sha256)

    root = Path(os.environ.get("INPUT_DIR", DEFAULT_INPUT_DIR)).resolve()
    path = (root / input_ref).resolve()

    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Input reference escapes the configured input directory.") from exc

    if not path.is_file():
        raise FileNotFoundError(f"Pack input was not found: {input_ref}")

    if path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"Pack image exceeds the {MAX_IMAGE_BYTES}-byte limit.")

    image = path.read_bytes()
    return _validated_image(image, input_ref, expected_sha256)
