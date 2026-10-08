"""Resolve and verify Round 3 content-addressed Pack inputs."""
from __future__ import annotations

import hashlib
import mimetypes
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = ROOT / "data" / "input"

SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


def resolve_image(input_ref: str, expected_sha256: str) -> tuple[bytes, str]:
    root = Path(os.environ.get("INPUT_DIR", DEFAULT_INPUT_DIR)).resolve()
    path = (root / input_ref).resolve()

    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError("Input reference escapes the configured input directory.") from exc

    if not path.is_file():
        raise FileNotFoundError(f"Pack input was not found: {input_ref}")

    image = path.read_bytes()
    actual_sha256 = hashlib.sha256(image).hexdigest()

    if actual_sha256.lower() != expected_sha256.lower():
        raise ValueError(f"Input hash mismatch for {input_ref}")

    mime_type, _ = mimetypes.guess_type(path.name)
    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise ValueError(f"Unsupported Pack image type: {mime_type or 'unknown'}")

    return image, mime_type
