VALID_QUALITY = {"GOOD", "POOR"}

MAX_DETECTIONS = 100
MAX_UNCERTAIN_ITEMS = 100
MAX_EVIDENCE_ITEMS = 50
MAX_TEXT_LENGTH = 1000


def _is_non_empty_string(value, max_length=MAX_TEXT_LENGTH):
    return (
        isinstance(value, str)
        and bool(value.strip())
        and len(value) <= max_length
    )


def validate_vision_result(value):
    if not isinstance(value, dict):
        raise ValueError("Vision response must be an object.")

    required_keys = {
        "imageQuality",
        "detectedItems",
        "uncertainItems",
        "evidence",
    }

    if set(value.keys()) != required_keys:
        raise ValueError(
            "Vision response has an invalid object shape."
        )

    if value["imageQuality"] not in VALID_QUALITY:
        raise ValueError(
            "Vision response has an invalid imageQuality value."
        )

    detected_items = value["detectedItems"]
    uncertain_items = value["uncertainItems"]
    evidence = value["evidence"]

    if (
        not isinstance(detected_items, list)
        or len(detected_items) > MAX_DETECTIONS
        or not isinstance(uncertain_items, list)
        or len(uncertain_items) > MAX_UNCERTAIN_ITEMS
        or not isinstance(evidence, list)
        or len(evidence) > MAX_EVIDENCE_ITEMS
    ):
        raise ValueError("Vision response is missing a required list.")

    for item in detected_items:
        if not isinstance(item, dict):
            raise ValueError("Invalid detected item.")

        if set(item.keys()) != {
            "sku",
            "name",
            "quantity",
            "confidence",
            "evidence",
        }:
            raise ValueError("Vision response contains an invalid detected item.")

        if not _is_non_empty_string(item["sku"], 128):
            raise ValueError("Invalid detected item SKU.")

        if not _is_non_empty_string(item["name"], 200):
            raise ValueError("Invalid detected item name.")

        if (
            not isinstance(item["quantity"], int)
            or isinstance(item["quantity"], bool)
            or item["quantity"] < 1
            or item["quantity"] > 100000
        ):
            raise ValueError("Invalid detected item quantity.")

        if (
            not isinstance(item["confidence"], (int, float))
            or isinstance(item["confidence"], bool)
            or not 0 <= item["confidence"] <= 1
        ):
            raise ValueError("Invalid detected item confidence.")

        if not _is_non_empty_string(item["evidence"]):
            raise ValueError("Invalid detected item evidence.")

    for item in uncertain_items:
        if not isinstance(item, dict):
            raise ValueError("Invalid uncertain item.")

        if set(item.keys()) != {"possibleSkus", "reason"}:
            raise ValueError("Vision response contains an invalid uncertain item.")

        possible_skus = item["possibleSkus"]

        if (
            not isinstance(possible_skus, list)
            or len(possible_skus) > MAX_DETECTIONS
            or any(not _is_non_empty_string(sku, 128) for sku in possible_skus)
        ):
            raise ValueError("Invalid uncertain item possibleSkus.")

        if not _is_non_empty_string(item["reason"]):
            raise ValueError("Invalid uncertain item reason.")

    if any(not _is_non_empty_string(item) for item in evidence):
        raise ValueError("Vision response contains invalid evidence.")

    return value
