# Pack Manager

Pack inspects an open merchant-fulfilled / 3PL package before sealing and creates a Round 3 Evidence Record for downstream agents. It accepts only units whose `subject.route` is `"mfn"`; other routes are rejected.

## Inputs and order source

For each accepted unit, Pack:

- Reads one open-box image input. The input must include a `ref` and its expected SHA-256.
- Resolves the referenced image and verifies its SHA-256 content address before inspection. Supported image MIME types are JPEG, PNG, and WebP.
- Reads expected order lines using the Pack-specific fixture resolver in [`order_resolver.py`](./order_resolver.py), backed by [`fixtures/orders.json`](./fixtures/orders.json).
- Reads previous evidence supplied in the request, including Receiving evidence, and records its IDs as upstream references.

The current Round 3 starter request does not expose `order_lines` directly. Accordingly, this implementation uses `agents/pack/fixtures/orders.json` through `order_resolver.py` as its Pack-specific fixture source. This is not a production order-system integration.

## Vision and packing decision

Pack sends the verified image and expected items to Gemini Vision in one inspection call. Gemini is used only for visual observation: it reports detected items, visible quantities, uncertainty, image quality, and visual evidence. It does not decide whether the order is correctly packed or recommend an outcome.

The Pack decision engine evaluates item presence, quantities, and unexpected items. Its results become these Evidence Record checks:

- `items_present`
- `quantities_correct`
- `no_extra_items`

The decision engine determines the outcome:

- `seal` when all checks pass
- `stop_and_fix` when a check fails
- `pending_review` when evidence is uncertain or inspection cannot be completed

The Evidence Record includes the image reference, observed items, image quality, uncertainty and vision evidence, order references, and previous-evidence references.

## Fail-open behavior

Pack does not fabricate a packing judgment when inspection evidence is missing or unusable:

- No open-box image produces `pending_review`.
- Image resolution, SHA-256 verification, MIME validation, Gemini configuration, or vision errors produce `pending_review` with error metadata.

Supply exactly one image with both `ref` and `sha256`; Pack cannot make a packing judgment without usable evidence.

## Configuration

- `GEMINI_API_KEY` is required for real Gemini inspection.
- `GEMINI_VISION_MODEL` is optional; it defaults to `gemini-3.5-flash-lite`.

## Testing

The Pack integration contract suite passes. Run it with:

```sh
pytest tests/integration/test_agent_contracts.py
```

The real UNIT-0006 open-box image at `data/input/UNIT-0006/pack/open_box.png` is a local test input and is not included in this repository. The image resolver tests can be run with:

```sh
pytest tests/test_pack_input_resolver.py
```
