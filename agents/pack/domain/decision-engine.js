const STATUS = Object.freeze({
  PASS: "PASS",
  FAIL: "FAIL",
  UNCERTAIN: "UNCERTAIN",
});

const CONFIDENCE_THRESHOLD = 0.8;

function aggregateStatus(statuses) {
  if (statuses.includes(STATUS.FAIL)) return STATUS.FAIL;
  if (statuses.includes(STATUS.UNCERTAIN)) return STATUS.UNCERTAIN;
  return STATUS.PASS;
}

function decidePacking({ expectedItems, detectedItems = [], imageQuality = "GOOD", uncertainItems = [] }) {
  if (!Array.isArray(expectedItems) || expectedItems.length === 0) {
    const reason = "Expected order items are unavailable, so packing cannot be verified.";
    return {
      checks: {
        presence: { status: STATUS.UNCERTAIN, reason },
        quantity: { status: STATUS.UNCERTAIN, reason },
        extra_items: { status: STATUS.UNCERTAIN, reason },
      },
      finalDecision: "REVIEW",
      reason,
    };
  }

  const qualityIsClear = imageQuality === "GOOD";
  const confidentDetections = detectedItems.filter(
    (item) => Number.isFinite(item.confidence) && item.confidence >= CONFIDENCE_THRESHOLD,
  );
  const uncertainForSku = (sku) => uncertainItems.some(
    (item) => !Array.isArray(item.possibleSkus) || item.possibleSkus.length === 0 || item.possibleSkus.includes(sku),
  );

  const lineChecks = expectedItems.map((expected) => {
    const matching = confidentDetections.filter((item) => item.sku === expected.sku);
    const observedQuantity = matching.reduce((sum, item) => sum + item.quantity, 0);
    const hasLowConfidenceMatch = detectedItems.some(
      (item) => item.sku === expected.sku && item.confidence < CONFIDENCE_THRESHOLD,
    );
    const identityUncertain = hasLowConfidenceMatch || uncertainForSku(expected.sku) || !qualityIsClear;

    let presenceStatus = STATUS.UNCERTAIN;
    let presenceReason;
    if (observedQuantity > 0) {
      presenceStatus = STATUS.PASS;
      presenceReason = `${expected.sku} was detected.`;
    } else if (!identityUncertain) {
      presenceStatus = STATUS.FAIL;
      presenceReason = `${expected.sku} was not detected.`;
    } else {
      presenceReason = `Evidence is insufficient to confirm whether ${expected.sku} is present.`;
    }

    let quantityStatus = STATUS.UNCERTAIN;
    let quantityReason;
    if (identityUncertain) {
      quantityReason = `The quantity of ${expected.sku} cannot be reliably established.`;
    } else if (observedQuantity === expected.quantity) {
      quantityStatus = STATUS.PASS;
      quantityReason = `${expected.sku}: expected ${expected.quantity}, detected ${observedQuantity}.`;
    } else {
      quantityStatus = STATUS.FAIL;
      quantityReason = `${expected.sku}: expected ${expected.quantity}, detected ${observedQuantity}.`;
    }

    return {
      presence: {
        sku: expected.sku,
        expectedQuantity: expected.quantity,
        observedQuantity,
        status: presenceStatus,
        reason: presenceReason,
      },
      quantity: {
        sku: expected.sku,
        expectedQuantity: expected.quantity,
        observedQuantity,
        status: quantityStatus,
        reason: quantityReason,
      },
    };
  });

  const knownExpectedSkus = new Set(expectedItems.map((item) => item.sku));
  const extraDetections = confidentDetections.filter((item) => !knownExpectedSkus.has(item.sku));
  const hasUnidentifiedDetection = uncertainItems.length > 0
    || detectedItems.some((item) => !Number.isFinite(item.confidence) || item.confidence < CONFIDENCE_THRESHOLD);

  let extraStatus = STATUS.UNCERTAIN;
  let extraReason;
  if (extraDetections.length > 0) {
    extraStatus = STATUS.FAIL;
    extraReason = `Unexpected item detected: ${extraDetections.map((item) => item.sku).join(", ")}.`;
  } else if (qualityIsClear && !hasUnidentifiedDetection) {
    extraStatus = STATUS.PASS;
    extraReason = "No unexpected items were detected.";
  } else {
    extraReason = "Evidence is insufficient to rule out unexpected items.";
  }

  const presence = {
    status: aggregateStatus(lineChecks.map((line) => line.presence.status)),
    reason: lineChecks.map((line) => line.presence.reason).join(" "),
    lines: lineChecks.map((line) => line.presence),
  };
  const quantity = {
    status: aggregateStatus(lineChecks.map((line) => line.quantity.status)),
    reason: lineChecks.map((line) => line.quantity.reason).join(" "),
    lines: lineChecks.map((line) => line.quantity),
  };
  const extraItems = { status: extraStatus, reason: extraReason };
  const statuses = [presence.status, quantity.status, extraItems.status];
  const finalDecision = statuses.includes(STATUS.FAIL)
    ? "STOP_AND_FIX"
    : statuses.includes(STATUS.UNCERTAIN)
      ? "REVIEW"
      : "SEAL";
  const reason = finalDecision === "SEAL"
    ? "All expected items and quantities were detected, with no unexpected items identified."
    : [presence, quantity, extraItems]
      .filter((check) => check.status !== STATUS.PASS)
      .map((check) => check.reason)
      .join(" ");

  return {
    checks: { presence, quantity, extra_items: extraItems },
    finalDecision,
    reason,
  };
}

export { CONFIDENCE_THRESHOLD, STATUS, decidePacking };