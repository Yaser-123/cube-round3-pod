"""Pack Manager decision engine.

Python port of the Round 2 decision-engine.js.
The decision logic is intentionally kept equivalent to the Round 2 behavior.
"""

from __future__ import annotations


STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"
STATUS_UNCERTAIN = "UNCERTAIN"

CONFIDENCE_THRESHOLD = 0.8


def aggregate_status(statuses):
    if STATUS_FAIL in statuses:
        return STATUS_FAIL
    if STATUS_UNCERTAIN in statuses:
        return STATUS_UNCERTAIN
    return STATUS_PASS


def decide_packing(
    *,
    expected_items,
    detected_items=None,
    image_quality="GOOD",
    uncertain_items=None,
):
    detected_items = detected_items or []
    uncertain_items = uncertain_items or []

    if not isinstance(expected_items, list) or not expected_items:
        reason = (
            "Expected order items are unavailable, "
            "so packing cannot be verified."
        )

        return {
            "checks": {
                "presence": {
                    "status": STATUS_UNCERTAIN,
                    "reason": reason,
                },
                "quantity": {
                    "status": STATUS_UNCERTAIN,
                    "reason": reason,
                },
                "extra_items": {
                    "status": STATUS_UNCERTAIN,
                    "reason": reason,
                },
            },
            "finalDecision": "REVIEW",
            "reason": reason,
        }

    quality_is_clear = image_quality == "GOOD"

    confident_detections = [
        item
        for item in detected_items
        if isinstance(item.get("confidence"), (int, float))
        and item["confidence"] >= CONFIDENCE_THRESHOLD
    ]

    def uncertain_for_sku(sku):
        return any(
            not isinstance(item.get("possibleSkus"), list)
            or len(item["possibleSkus"]) == 0
            or sku in item["possibleSkus"]
            for item in uncertain_items
        )

    line_checks = []

    for expected in expected_items:
        expected_sku = expected["sku"]
        expected_quantity = expected["quantity"]

        matching = [
            item
            for item in confident_detections
            if item.get("sku") == expected_sku
        ]

        observed_quantity = sum(
            item["quantity"]
            for item in matching
        )

        has_low_confidence_match = any(
            item.get("sku") == expected_sku
            and item.get("confidence") < CONFIDENCE_THRESHOLD
            for item in detected_items
        )

        identity_uncertain = (
            has_low_confidence_match
            or uncertain_for_sku(expected_sku)
            or not quality_is_clear
        )

        presence_status = STATUS_UNCERTAIN
        presence_reason = None

        if observed_quantity > 0:
            presence_status = STATUS_PASS
            presence_reason = f"{expected_sku} was detected."
        elif not identity_uncertain:
            presence_status = STATUS_FAIL
            presence_reason = f"{expected_sku} was not detected."
        else:
            presence_reason = (
                f"Evidence is insufficient to confirm "
                f"whether {expected_sku} is present."
            )

        quantity_status = STATUS_UNCERTAIN
        quantity_reason = None

        if identity_uncertain:
            quantity_reason = (
                f"The quantity of {expected_sku} "
                "cannot be reliably established."
            )
        elif observed_quantity == expected_quantity:
            quantity_status = STATUS_PASS
            quantity_reason = (
                f"{expected_sku}: expected {expected_quantity}, "
                f"detected {observed_quantity}."
            )
        else:
            quantity_status = STATUS_FAIL
            quantity_reason = (
                f"{expected_sku}: expected {expected_quantity}, "
                f"detected {observed_quantity}."
            )

        line_checks.append({
            "presence": {
                "sku": expected_sku,
                "expectedQuantity": expected_quantity,
                "observedQuantity": observed_quantity,
                "status": presence_status,
                "reason": presence_reason,
            },
            "quantity": {
                "sku": expected_sku,
                "expectedQuantity": expected_quantity,
                "observedQuantity": observed_quantity,
                "status": quantity_status,
                "reason": quantity_reason,
            },
        })

    known_expected_skus = {
        item["sku"]
        for item in expected_items
    }

    extra_detections = [
        item
        for item in confident_detections
        if item.get("sku") not in known_expected_skus
    ]

    has_unidentified_detection = (
        len(uncertain_items) > 0
        or any(
            not isinstance(item.get("confidence"), (int, float))
            or item["confidence"] < CONFIDENCE_THRESHOLD
            for item in detected_items
        )
    )

    extra_status = STATUS_UNCERTAIN
    extra_reason = None

    if extra_detections:
        extra_status = STATUS_FAIL
        extra_reason = (
            "Unexpected item detected: "
            + ", ".join(
                item["sku"] for item in extra_detections
            )
            + "."
        )
    elif quality_is_clear and not has_unidentified_detection:
        extra_status = STATUS_PASS
        extra_reason = "No unexpected items were detected."
    else:
        extra_reason = (
            "Evidence is insufficient to rule out unexpected items."
        )

    presence = {
        "status": aggregate_status(
            [line["presence"]["status"] for line in line_checks]
        ),
        "reason": " ".join(
            line["presence"]["reason"]
            for line in line_checks
        ),
        "lines": [
            line["presence"]
            for line in line_checks
        ],
    }

    quantity = {
        "status": aggregate_status(
            [line["quantity"]["status"] for line in line_checks]
        ),
        "reason": " ".join(
            line["quantity"]["reason"]
            for line in line_checks
        ),
        "lines": [
            line["quantity"]
            for line in line_checks
        ],
    }

    extra_items = {
        "status": extra_status,
        "reason": extra_reason,
    }

    statuses = [
        presence["status"],
        quantity["status"],
        extra_items["status"],
    ]

    if STATUS_FAIL in statuses:
        final_decision = "STOP_AND_FIX"
    elif STATUS_UNCERTAIN in statuses:
        final_decision = "REVIEW"
    else:
        final_decision = "SEAL"

    if final_decision == "SEAL":
        reason = (
            "All expected items and quantities were detected, "
            "with no unexpected items identified."
        )
    else:
        reason = " ".join(
            check["reason"]
            for check in [presence, quantity, extra_items]
            if check["status"] != STATUS_PASS
        )

    return {
        "checks": {
            "presence": presence,
            "quantity": quantity,
            "extra_items": extra_items,
        },
        "finalDecision": final_decision,
        "reason": reason,
    }
