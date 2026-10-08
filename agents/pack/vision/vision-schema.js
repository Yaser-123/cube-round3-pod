const VALID_QUALITY = new Set(["GOOD", "POOR"]);
const MAX_DETECTIONS = 100;
const MAX_UNCERTAIN_ITEMS = 100;
const MAX_EVIDENCE_ITEMS = 50;
const MAX_TEXT_LENGTH = 1000;

function isRecord(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function hasExactKeys(value, expectedKeys) {
  const keys = Object.keys(value);
  return keys.length === expectedKeys.length && expectedKeys.every((key) => keys.includes(key));
}

function isNonEmptyString(value, maxLength = MAX_TEXT_LENGTH) {
  return typeof value === "string" && value.trim().length > 0 && value.length <= maxLength;
}

function validateVisionResult(value) {
  if (!isRecord(value) || !hasExactKeys(value, ["imageQuality", "detectedItems", "uncertainItems", "evidence"])
    || !VALID_QUALITY.has(value.imageQuality)) {
    throw new Error("Vision response has an invalid object shape or imageQuality value.");
  }
  if (!Array.isArray(value.detectedItems) || value.detectedItems.length > MAX_DETECTIONS
    || !Array.isArray(value.uncertainItems) || value.uncertainItems.length > MAX_UNCERTAIN_ITEMS
    || !Array.isArray(value.evidence) || value.evidence.length > MAX_EVIDENCE_ITEMS) {
    throw new Error("Vision response is missing a required list.");
  }

  for (const item of value.detectedItems) {
    if (!isRecord(item) || !hasExactKeys(item, ["sku", "name", "quantity", "confidence", "evidence"])
      || !isNonEmptyString(item.sku, 128)
      || !isNonEmptyString(item.name, 200)
      || !Number.isSafeInteger(item.quantity) || item.quantity < 1 || item.quantity > 100000
      || !Number.isFinite(item.confidence) || item.confidence < 0 || item.confidence > 1
      || !isNonEmptyString(item.evidence)) {
      throw new Error("Vision response contains an invalid detected item.");
    }
  }

  for (const item of value.uncertainItems) {
    if (!isRecord(item) || !hasExactKeys(item, ["possibleSkus", "reason"])
      || !Array.isArray(item.possibleSkus) || item.possibleSkus.length > MAX_DETECTIONS
      || item.possibleSkus.some((sku) => !isNonEmptyString(sku, 128))
      || !isNonEmptyString(item.reason)) {
      throw new Error("Vision response contains an invalid uncertain item.");
    }
  }
  if (value.evidence.some((item) => !isNonEmptyString(item))) {
    throw new Error("Vision response contains invalid evidence.");
  }

  return value;
}

export { MAX_EVIDENCE_ITEMS, validateVisionResult };