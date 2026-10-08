import { MAX_EVIDENCE_ITEMS, validateVisionResult } from "./vision-schema.js";

const DEFAULT_MODEL = "gemini-3.5-flash-lite";
const DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta";
const SUPPORTED_IMAGE_TYPES = new Set(["image/jpeg", "image/png", "image/webp"]);

const RESPONSE_SCHEMA = {
  type: "OBJECT",
  required: ["imageQuality", "detectedItems", "uncertainItems", "evidence"],
  properties: {
    imageQuality: { type: "STRING", enum: ["GOOD", "POOR"] },
    detectedItems: {
      type: "ARRAY",
      items: {
        type: "OBJECT",
        required: ["sku", "name", "quantity", "confidence", "evidence"],
        properties: {
          sku: { type: "STRING" },
          name: { type: "STRING" },
          quantity: { type: "INTEGER" },
          confidence: { type: "NUMBER" },
          evidence: { type: "STRING" },
        },
      },
    },
    uncertainItems: {
      type: "ARRAY",
      items: {
        type: "OBJECT",
        required: ["possibleSkus", "reason"],
        properties: {
          possibleSkus: { type: "ARRAY", items: { type: "STRING" } },
          reason: { type: "STRING" },
        },
      },
    },
    evidence: { type: "ARRAY", items: { type: "STRING" } },
  },
};

function sanitizeMessage(value, apiKey) {
  let message = typeof value === "string" ? value : String(value ?? "Unknown error");
  if (apiKey) message = message.split(apiKey).join("[REDACTED]");
  return message
    .replace(/Bearer\s+\S+/gi, "Bearer [REDACTED]")
    .slice(0, 1000);
}

function providerError(message, { status, code, detail } = {}, apiKey) {
  const error = new Error(sanitizeMessage(message, apiKey));
  error.diagnostic = {
    message: sanitizeMessage(detail || message, apiKey),
    ...(Number.isInteger(status) ? { status } : {}),
    ...(typeof code === "string" && /^[A-Za-z0-9_.-]{1,100}$/.test(code) ? { code } : {}),
  };
  if (error.diagnostic.code) error.code = error.diagnostic.code;
  return error;
}

async function readErrorMessage(response) {
  try {
    const body = await response.json();
    return typeof body?.error?.message === "string" ? body.error.message : undefined;
  } catch {
    return undefined;
  }
}

function extractGeminiText(payload) {
  if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
    throw new Error("Gemini returned an invalid response.");
  }
  if (payload.promptFeedback?.blockReason) {
    throw new Error(`Gemini blocked the image request: ${payload.promptFeedback.blockReason}.`);
  }
  const candidate = payload.candidates?.[0];
  if (!candidate || candidate.finishReason === "SAFETY" || candidate.finishReason === "RECITATION") {
    throw new Error("Gemini did not return usable visual analysis.");
  }
  const text = candidate.content?.parts
    ?.filter((part) => typeof part?.text === "string")
    .map((part) => part.text)
    .join("");
  if (!text) throw new Error("Gemini response did not contain structured output.");
  return text;
}

function parseGeminiVisionResponse(payload) {
  const text = extractGeminiText(payload);
  let parsed;
  try {
    parsed = JSON.parse(text);
  } catch {
    throw new Error("Gemini response did not contain valid JSON.");
  }

  const result = validateVisionResult(parsed);
  const evidence = [...result.evidence, "Gemini generateContent vision analysis completed."];
  if (evidence.length > MAX_EVIDENCE_ITEMS) {
    throw new Error("Gemini response exceeded the evidence limit.");
  }
  return { ...result, evidence };
}

function normalizeBaseUrl(value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error("GEMINI_BASE_URL must be a valid HTTPS URL.");
  }
  const isLocalHttp = url.protocol === "http:"
    && (url.hostname === "localhost" || url.hostname === "127.0.0.1" || url.hostname === "::1");
  if ((url.protocol !== "https:" && !isLocalHttp) || url.username || url.password || url.search || url.hash) {
    throw new Error("GEMINI_BASE_URL must use HTTPS and must not contain credentials or query parameters.");
  }
  return url.toString().replace(/\/+$/, "");
}

function createGeminiVisionService({
  apiKey = process.env.GEMINI_API_KEY,
  model = process.env.GEMINI_VISION_MODEL || DEFAULT_MODEL,
  baseUrl = DEFAULT_BASE_URL,
  apiKeySource = "unknown",
  fetchImpl = globalThis.fetch,
} = {}) {
  const normalizedBaseUrl = normalizeBaseUrl(baseUrl);
  const endpoint = `${normalizedBaseUrl}/models/${encodeURIComponent(model)}:generateContent`;
  const configurationDiagnostics = Object.freeze({
    apiKeyPresent: typeof apiKey === "string" && apiKey.length > 0,
    apiKeySource,
    baseUrl: normalizedBaseUrl,
    endpoint,
    model,
  });

  return {
    provider: "gemini",
    configurationDiagnostics,
    analyze: async ({ image, mimeType, expectedItems }, { signal } = {}) => {
      if (typeof apiKey !== "string" || !apiKey.trim()) {
        throw new Error("GEMINI_API_KEY is required to use the Gemini vision provider.");
      }
      if (typeof model !== "string" || !model.trim()) {
        throw new Error("GEMINI_VISION_MODEL must be a non-empty model name.");
      }
      if (typeof fetchImpl !== "function") {
        throw new Error("This Node.js runtime does not provide fetch for the Gemini vision provider.");
      }
      if (!Buffer.isBuffer(image) || !SUPPORTED_IMAGE_TYPES.has(mimeType)) {
        throw new Error("Gemini vision provider received an unsupported image.");
      }
      if (!Array.isArray(expectedItems) || expectedItems.length === 0) {
        throw new Error("Gemini vision provider requires expected order items.");
      }

      const catalog = expectedItems.map(({ sku, name, quantity }) => ({ sku, name, quantity }));
      const requestBody = {
        systemInstruction: {
          parts: [{
            text: "Analyze only visible package contents and image quality. Return detected item identities, visible quantities, uncertainty, and concise visual evidence. Use an expected SKU only when the image supports that identity. For a clearly visible item not in the expected catalog, use a descriptive UNEXPECTED:<label> SKU. Put ambiguous identities in uncertainItems. Confidence is visual confidence from 0 to 1, not certainty about the order. Do not compare detections against order quantities, evaluate whether packing is correct, or recommend or choose any decision, action, PASS, FAIL, SEAL, STOP_AND_FIX, or REVIEW.",
          }],
        },
        contents: [{
          role: "user",
          parts: [
            {
              text: `Expected catalog for visual identification only:\n${JSON.stringify(catalog)}\nReturn visual observations only.`,
            },
            {
              inlineData: {
                mimeType,
                data: image.toString("base64"),
              },
            },
          ],
        }],
        generationConfig: {
          responseMimeType: "application/json",
          responseSchema: RESPONSE_SCHEMA,
        },
      };

      let response;
      try {
        response = await fetchImpl(endpoint, {
          method: "POST",
          headers: {
            "content-type": "application/json",
            "x-goog-api-key": apiKey,
          },
          body: JSON.stringify(requestBody),
          signal,
        });
      } catch (error) {
        throw providerError("Gemini vision request failed.", {
          code: error?.code,
          detail: error?.message || error,
        }, apiKey);
      }

      if (!response.ok) {
        throw providerError(`Gemini API returned HTTP ${response.status}.`, {
          status: response.status,
          detail: response.status === 401 || response.status === 403
            ? undefined
            : await readErrorMessage(response),
        }, apiKey);
      }

      let payload;
      try {
        payload = await response.json();
      } catch (error) {
        throw providerError("Gemini API returned invalid JSON.", { detail: error?.message }, apiKey);
      }
      try {
        return parseGeminiVisionResponse(payload);
      } catch (error) {
        throw providerError("Gemini vision response could not be processed.", {
          detail: error?.message,
        }, apiKey);
      }
    },
  };
}

export { createGeminiVisionService, parseGeminiVisionResponse };
