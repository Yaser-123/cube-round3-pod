"""Gemini vision provider for Pack Manager."""
from __future__ import annotations

import base64
import json
import os
import re
from urllib.parse import quote, urlparse

import requests

from .schema import MAX_EVIDENCE_ITEMS, validate_vision_result


DEFAULT_MODEL = "gemini-3.5-flash-lite"
DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"

SUPPORTED_IMAGE_TYPES = {
    "image/jpeg",
    "image/png",
    "image/webp",
}


def sanitize_message(value, api_key=None):
    message = str(value or "Unknown error")

    if api_key:
        message = message.replace(api_key, "[REDACTED]")

    message = re.sub(r"Bearer\s+\S+", "Bearer [REDACTED]", message, flags=re.IGNORECASE)
    return message[:1000]


def normalize_base_url(value):
    parsed = urlparse(value)

    is_local_http = (
        parsed.scheme == "http"
        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
    )

    if (
        parsed.scheme != "https"
        and not is_local_http
    ):
        raise ValueError(
            "GEMINI_BASE_URL must use HTTPS."
        )

    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError(
            "GEMINI_BASE_URL must not contain credentials or query parameters."
        )

    return value.rstrip("/")


def create_gemini_vision_service(
    api_key=None,
    model=None,
    base_url=DEFAULT_BASE_URL,
):
    api_key = api_key or os.environ.get("GEMINI_API_KEY")
    model = model or os.environ.get(
        "GEMINI_VISION_MODEL",
        DEFAULT_MODEL,
    )

    normalized_base_url = normalize_base_url(base_url)

    if not api_key or not api_key.strip():
        raise ValueError(
            "GEMINI_API_KEY is required to use the Gemini vision provider."
        )

    if not model or not model.strip():
        raise ValueError(
            "GEMINI_VISION_MODEL must be a non-empty model name."
        )

    endpoint = (
        f"{normalized_base_url}/models/"
        f"{quote(model, safe='')}:generateContent"
    )

    return {
        "provider": "gemini",
        "configurationDiagnostics": {
            "apiKeyPresent": bool(api_key),
            "baseUrl": normalized_base_url,
            "endpoint": endpoint,
            "model": model,
        },
        "analyze": lambda image, mime_type, expected_items:
            analyze_gemini(
                image=image,
                mime_type=mime_type,
                expected_items=expected_items,
                api_key=api_key,
                endpoint=endpoint,
            ),
    }


def analyze_gemini(
    *,
    image,
    mime_type,
    expected_items,
    api_key,
    endpoint,
):
    if not isinstance(image, bytes):
        raise ValueError(
            "Gemini vision provider received invalid image bytes."
        )

    if mime_type not in SUPPORTED_IMAGE_TYPES:
        raise ValueError(
            "Gemini vision provider received an unsupported image."
        )

    if not isinstance(expected_items, list) or not expected_items:
        raise ValueError(
            "Gemini vision provider requires expected order items."
        )

    catalog = [
        {
            "sku": item["sku"],
            "name": item.get("name", item["sku"]),
            "quantity": item["quantity"],
        }
        for item in expected_items
    ]

    request_body = {
        "systemInstruction": {
            "parts": [{
                "text": (
                    "Analyze only visible package contents and image quality. "
                    "Return detected item identities, visible quantities, "
                    "uncertainty, and concise visual evidence. "
                    "Use an expected SKU only when the image supports that identity. "
                    "For a clearly visible item not in the expected catalog, "
                    "use a descriptive UNEXPECTED:<label> SKU. "
                    "Put ambiguous identities in uncertainItems. "
                    "Confidence is visual confidence from 0 to 1, "
                    "not certainty about the order. "
                    "Do not compare detections against order quantities, "
                    "evaluate whether packing is correct, or recommend or choose "
                    "any decision, action, PASS, FAIL, SEAL, STOP_AND_FIX, or REVIEW."
                )
            }]
        },
        "contents": [{
            "role": "user",
            "parts": [
                {
                    "text": (
                        "Expected catalog for visual identification only:\n"
                        f"{json.dumps(catalog)}\n"
                        "Return visual observations only."
                    )
                },
                {
                    "inlineData": {
                        "mimeType": mime_type,
                        "data": base64.b64encode(image).decode("ascii"),
                    }
                },
            ],
        }],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "imageQuality": {
                        "type": "STRING",
                        "enum": ["GOOD", "POOR"],
                    },
                    "detectedItems": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "sku": {"type": "STRING"},
                                "name": {"type": "STRING"},
                                "quantity": {"type": "INTEGER"},
                                "confidence": {"type": "NUMBER"},
                                "evidence": {"type": "STRING"},
                            },
                            "required": [
                                "sku",
                                "name",
                                "quantity",
                                "confidence",
                                "evidence",
                            ],
                        },
                    },
                    "uncertainItems": {
                        "type": "ARRAY",
                        "items": {
                            "type": "OBJECT",
                            "properties": {
                                "possibleSkus": {
                                    "type": "ARRAY",
                                    "items": {"type": "STRING"},
                                },
                                "reason": {"type": "STRING"},
                            },
                            "required": [
                                "possibleSkus",
                                "reason",
                            ],
                        },
                    },
                    "evidence": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
                "required": [
                    "imageQuality",
                    "detectedItems",
                    "uncertainItems",
                    "evidence",
                ],
            },
        },
    }

    try:
        response = requests.post(
            endpoint,
            headers={
                "content-type": "application/json",
                "x-goog-api-key": api_key,
            },
            json=request_body,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise RuntimeError(
            sanitize_message(
                f"Gemini vision request failed: {exc}",
                api_key,
            )
        ) from exc

    if not response.ok:
        if response.status_code in {401, 403}:
            detail = "Gemini authentication/authorization failed."
        else:
            try:
                detail = response.json().get("error", {}).get("message")
            except Exception:
                detail = None

        raise RuntimeError(
            sanitize_message(
                detail or f"Gemini API returned HTTP {response.status_code}.",
                api_key,
            )
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError(
            "Gemini API returned invalid JSON."
        ) from exc

    result = parse_gemini_vision_response(payload)

    evidence = list(result["evidence"])
    evidence.append("Gemini generateContent vision analysis completed.")

    if len(evidence) > MAX_EVIDENCE_ITEMS:
        raise ValueError(
            "Gemini response exceeded the evidence limit."
        )

    result["evidence"] = evidence
    return result


def parse_gemini_vision_response(payload):
    if not isinstance(payload, dict):
        raise ValueError(
            "Gemini returned an invalid response."
        )

    block_reason = (
        payload.get("promptFeedback", {})
        .get("blockReason")
    )

    if block_reason:
        raise ValueError(
            f"Gemini blocked the image request: {block_reason}."
        )

    candidates = payload.get("candidates", [])
    candidate = candidates[0] if candidates else None

    if (
        not candidate
        or candidate.get("finishReason") in {"SAFETY", "RECITATION"}
    ):
        raise ValueError(
            "Gemini did not return usable visual analysis."
        )

    parts = candidate.get("content", {}).get("parts", [])

    text = "".join(
        part["text"]
        for part in parts
        if isinstance(part, dict)
        and isinstance(part.get("text"), str)
    )

    if not text:
        raise ValueError(
            "Gemini response did not contain structured output."
        )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(
            "Gemini response did not contain valid JSON."
        ) from exc

    return validate_vision_result(parsed)
