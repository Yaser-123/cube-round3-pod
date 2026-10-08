"""Pack Manager: Round 3 agent entry point."""

from __future__ import annotations

import os
import time

from shared.utils.records import build_output, build_record, check, pending_output
from shared.utils.server import make_app

from agents.pack.domain.decision_engine import decide_packing
from agents.pack.input_resolver import resolve_image
from agents.pack.order_resolver import parse_order_lines, resolve_order
from agents.pack.vision.gemini_vision_service import create_gemini_vision_service

STAGE = "pack"
AGENT_ID = "pack-manager@1"
MODEL_NAME = os.environ.get("GEMINI_VISION_MODEL", "gemini-3.5-flash-lite")


def _previous_record_ids(request: dict) -> list[str]:
    return [
        record["record_id"]
        for record in request.get("previous_evidence", [])
        if isinstance(record, dict) and record.get("record_id")
    ]


def _find_image_input(request: dict) -> dict | None:
    images = [
        item
        for item in request.get("inputs", [])
        if isinstance(item, dict) and item.get("kind") == "image"
    ]

    if not images:
        return None

    if len(images) > 1:
        raise ValueError("Pack expects one open-box image.")

    image = images[0]

    if not image.get("ref") or not image.get("sha256"):
        raise ValueError("Pack image input requires ref and sha256.")

    return image


def _map_check(
    check_key: str,
    result: dict,
    *,
    evidence_refs: list[str],
) -> dict:
    status = result["status"]

    return check(
        check_key,
        status,
        None,
        expected=result.get("lines"),
        observed=result.get("lines"),
        detail=result.get("reason", ""),
        evidence_refs=evidence_refs,
    )


def handle(request: dict) -> dict:
    subject = request["subject"]
    org_id = subject["org_id"]
    unit_id = subject["subject_id"]

    if subject.get("route") != "mfn":
        raise LookupError(
            f"Pack only accepts merchant-fulfilled / 3PL units; "
            f"route={subject.get('route')!r}"
        )

    previous_refs = _previous_record_ids(request)

    # Resolve the tenant-scoped order before checking stage inputs.
    # A wrong tenant must be rejected, never hidden behind a pending result.
    order = resolve_order(org_id, unit_id)
    expected_items = parse_order_lines(order["order_lines"])

    # The generic Round 3 contract test deliberately supplies no image.
    # Do not fabricate a packing judgment in that situation.
    image_input = _find_image_input(request)

    if image_input is None:
        return pending_output(
            request,
            code="pack_input_missing",
            message="No open-box image was supplied for Pack inspection.",
            retryable=False,
            agent_id=AGENT_ID,
        )

    try:
        image_bytes, mime_type = resolve_image(
            image_input["ref"],
            image_input["sha256"],
        )

        started = time.perf_counter()

        vision = create_gemini_vision_service()

        vision_result = vision["analyze"](
            image=image_bytes,
            mime_type=mime_type,
            expected_items=expected_items,
        )
    except Exception as exc:
        return pending_output(
            request,
            code="pack_inspection_error",
            message=str(exc),
            retryable=True,
            agent_id=AGENT_ID,
        )

    latency_ms = int((time.perf_counter() - started) * 1000)

    decision = decide_packing(
        expected_items=expected_items,
        detected_items=vision_result["detectedItems"],
        image_quality=vision_result["imageQuality"],
        uncertain_items=vision_result["uncertainItems"],
    )

    evidence_refs = [image_input["ref"]]

    checks = [
        _map_check(
            "items_present",
            decision["checks"]["presence"],
            evidence_refs=evidence_refs,
        ),
        _map_check(
            "quantities_correct",
            decision["checks"]["quantity"],
            evidence_refs=evidence_refs,
        ),
        _map_check(
            "no_extra_items",
            decision["checks"]["extra_items"],
            evidence_refs=evidence_refs,
        ),
    ]

    outcome_map = {
        "SEAL": "seal",
        "STOP_AND_FIX": "stop_and_fix",
        "REVIEW": "pending_review",
    }

    outcome = outcome_map[decision["finalDecision"]]

    record = build_record(
        request,
        agent_id=AGENT_ID,
        record_id=f"PCK-{request['request_id'].replace(':', '-')}",
        captured_at=request.get("context", {}).get(
            "captured_at",
            "unknown",
        ),
        checks=checks,
        outcome=outcome,
        reason=decision["reason"],
        model={
            "name": "gemini",
            "version": MODEL_NAME,
            "calls": 1,
        },
        unit_scope="order",
        refs={
            "order_id": order["order_id"],
        },
        inputs=[image_input],
        upstream_refs=previous_refs,
        latency_ms=latency_ms,
        payload={
            "channel": order["channel"],
            "order_lines": order["order_lines"],
            "observed_in_box": vision_result["detectedItems"],
            "image_quality": vision_result["imageQuality"],
            "uncertain_items": vision_result["uncertainItems"],
            "vision_evidence": vision_result["evidence"],
        },
        needs_human=outcome == "pending_review",
    )

    return build_output(record)


app = make_app(STAGE, handle)
