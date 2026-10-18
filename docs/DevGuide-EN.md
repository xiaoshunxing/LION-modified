# LION modified — Developer Guide (English)

> For whoever continues this work next. After reading this you should be able to reproduce the environment,
> run the tests, change the code, rebuild the package, and know what may and may not be touched.
>
> - Repository root: this repository
> - Artifact: `dist\LION-modified-3.3.7.nvda-addon` (SHA256 in the sibling `.files.txt` or `tests\artifacts\build-report.json`)
> - Upstream references: [LION](https://github.com/anmi2004/LION), [NVDA 2026.2 sources](https://github.com/nvaccess/nvda/tree/release-2026.2)
> - Every number below was measured on this machine (2026-10-16); nothing is estimated.
> - Document order: **original first, fork below** — same convention as the readme and `manifest.ini`.

---

# Part 1: the original LION

The original LION is an NVDA global plugin that OCRs a screen area at a fixed interval and speaks only when the
text changes. It is used for subtitles (YouTube / Netflix / Bilibili / local players / live TV) and for text a
screen reader cannot reach (in-game menus, for example).

Its core design:

| Aspect | Original approach |
|---|---|
| OCR backend | ships `PaddleOCR-json.exe` (296 MB) plus models, talks to it over a `subprocess` pipe |
| Capture area | one area: `target` (navigator object / whole screen / current window / current control) + four percentage crops |
| Deduplication | speaks only when `SequenceMatcher(None, prev, cur).ratio() < threshold` |
| Filtering | regexes remove matched content from the joined text |
| Dependencies | bundled PIL (13.9 MB) |
| Gesture | `NVDA+Alt+N` |
| Licence | GNU GPL v2 or later; authors Stefan Moisei, Cary-rowen, anmi2004, hwf1324 |

The original's main constraints: a 310 MB add-on, a mandatory local engine process, exactly one OCR area, and no
way to hear several areas at once.

---

# Part 2: what this fork changes

## 1. Summary

| | Original LION 3.3.0 | LION modified 3.3.6 |
|---|---|---|
| OCR backend | bundled `PaddleOCR-json.exe` over a pipe | external PaddleOCR HTTP service, `POST /ocr` |
| Capture areas | one | **a list of areas: add/edit/remove/reorder, each with a check box** |
| Speech | always interrupting | optional **queued speech** (`speech.Spri.NEXT`), no interruptions |
| Target per area | one global target | **each area picks its own** (navigator object / whole screen / current window / current control) |
| Deduplication | one global `prevString` | **independent per area** |
| Failure reporting | one announcement globally | **one announcement per area**, isolated |
| Add-on ID | `LION` | `LION-modified` (coexists with the original) |
| Config section | `lion` | `LION-modified` |
| Add-on size | 310.4 MB | **59.4 KB** |
| Third-party payload | bundled PIL 13.9 MB + engine 296.4 MB | none (stdlib + NVDA's own PIL) |

### Key decisions and why

1. **stdlib `urllib.request`, not `requests`.** NVDA ships `requests`/`urllib3` inside
   `<NVDA install dir>\library.zip`, but those are NVDA's internal, versioned libraries. An add-on should not
   depend on the host's private packaging. The cost: no connection pooling (irrelevant on loopback) and
   hand-written timeout/error classification.

2. **The bundled `PIL/` was deleted.** It lived at `globalPlugins/lion/PIL/` while the code does
   `from PIL import ImageGrab`, which resolves to **NVDA's own PIL** (`library.zip` holds 105 `PIL/*` entries
   plus `PIL._imaging.pyd`). Those 13.9 MB were never used.

3. **The bundled engine was deleted rather than kept behind a switch.** The requirement is an external
   service; keeping a 296 MB second backend would double payload, test surface and configuration. For offline
   use, fall back to the original LION — both add-ons can coexist.

4. **The three capability flags are sent explicitly as `false`.** The server's pipeline config enables
   `use_doc_preprocessor` / `use_textline_orientation` so those capabilities *can* be requested, and PaddleX
   applies config defaults when a field is omitted. Omitting them made every request run UVDoc unwarping:
   measured 146 ms → 329 ms for a 1000×700 region.

5. **The area list is stored as one JSON string.** NVDA's `config.conf.spec` is configobj; with
   `list_values=True` a value containing commas is split into a list, and configobj has no good way to express an
   array of objects. Storing one JSON string makes **each write atomic** — a user can never end up with a
   half-updated area list — and all validation lives in `regions.loadFromJson()`; corrupted config degrades to
   the default instead of raising.

6. **`regions.py` imports no NVDA module.** The area maths (cropping, normalisation, migration) is the most
   error-prone part and the most valuable to test. Base-rectangle resolution is injected through a
   `resolveBase` callback, so 35 pure-logic cases run under plain Python with no NVDA.

7. **Queued speech uses `speech.Spri.NEXT`.** `ui.message(text, speechPriority=...)` supports priorities
   directly, and `NEXT` means exactly "speak after the current utterance finishes" (see NVDA's
   `speech/priorities.py`). No custom threads or sleeps are needed. With queueing off, `NOW` (interrupt) is
   used, which is the old behaviour.

8. **`SequenceMatcher` is constructed with `autojunk=False`.** `difflib` enables "autojunk" for sequences of
   length ≥ 200 and treats elements appearing more than 1% of the time as noise. CJK text has very repetitive
   common characters, so long subtitles risk being judged "identical" and never spoken again. Recognition
   results are only tens of characters, so disabling the heuristic is accurate and essentially free.

## 2. Layout

```
LION-modified/
├── src/                                  # packaging root: zipped verbatim into the .nvda-addon
│   ├── manifest.ini                      # declaration (original description/log first, fork below)
│   ├── globalPlugins/lion/
│   │   ├── __init__.py                   # GlobalPlugin: confspec, migration, multi-area loop, queued speech
│   │   ├── regions.py                    # NEW: area model — parse/normalise/crop geometry/migration
│   │   ├── ocrClient.py                  # NEW: external PaddleOCR HTTP client
│   │   └── lionGui.py                    # NEW settings panel + area editor dialog
│   ├── doc/{zh_CN,en}/readme.md          # original text first, fork section below
│   ├── doc/{zh_CN,en}/readme.html        # generated by scripts/make_readme_html.py
│   ├── doc/style.css
│   └── locale/zh_CN/{manifest.ini,LC_MESSAGES/{nvda.po,nvda.mo}}
├── scripts/
│   ├── build_addon.ps1                   # hygiene → html → zip → re-inspect → SHA256
│   └── make_readme_html.py               # readme.md → readme.html (no markdown library on this host)
├── tests/
│   ├── nvda_stubs.py                     # NVDA environment stubs + configobj resolution
│   ├── vendor/configobj/                 # pure-Python configobj copy (manifest/spec validation)
│   ├── test_ocr_client.py                # 34 cases: HTTP client (incl. live service)
│   ├── test_regions.py                   # 35 cases: area model, pure logic (offline)
│   ├── test_plugin_integration.py        # 58 cases: multi-area loop, queued speech, migration, live end-to-end
│   ├── test_manifest_and_config.py       # 31 cases: declaration and confspec validation
│   ├── test_package.py                   # 17 cases: structural validation of the .nvda-addon
│   ├── probe_service.py                  # liveness + one real OCR call
│   ├── bench_http.py                     # end-to-end benchmark (area × format)
│   └── artifacts/                        # measured output
├── docs/                                 # this file and the Chinese original
└── dist/                                 # LION-modified-3.3.7.nvda-addon + file listing
```

## 3. Environment and reproduction

**No new environment and no system changes are needed.** Example test environment:

| Purpose | Path |
|---|---|
| Python for tests/benchmarks | `python` (3.12; `PIL` is optional for the real-capture cases) |
| Target NVDA | `%ProgramFiles%\NVDA` (**2026.2**, Python **3.13**) |
| OCR service | `http://127.0.0.1:23353`, deployed under `<service-deploy-dir>` |
| Service token | `<service-deploy-dir>\config\api_token.txt` |

### Run everything

```powershell
cd <repository-root>
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONDONTWRITEBYTECODE = '1'        # REQUIRED: otherwise __pycache__ appears under src/ and packaging fails
python -W ignore::ResourceWarning -m unittest discover -s tests -p 'test_*.py'
```

- `PYTHONDONTWRITEBYTECODE=1` is mandatory: the tests import `src/globalPlugins/lion`, and without it
  `src/` gains `__pycache__`, which both `build_addon.ps1` and `test_package.py` reject.
- Most cases are fully offline. Two groups touch the live service:
  `test_ocr_client.LiveServiceTests` and `test_plugin_integration.ServiceProbeTests` (the latter runs the whole
  chain: real capture → real encoding → real HTTP → parsing). Both **skip** when the service is unreachable.
- Fully offline: `$env:LION_SKIP_SERVICE_PROBE = '1'` and `$env:LION_TEST_LIVE = '0'`.
- Retarget with `$env:LION_TEST_URL` / `$env:LION_TEST_TOKEN` / `$env:LION_TEST_TOKEN_FILE`.

### Do not install packages into the conda environment

This project does not require `pip install configobj` (avoid polluting a shared environment).
`tests/vendor/configobj/` holds a pure-Python copy (from
[DiffSK/configobj](https://github.com/DiffSK/configobj), 5.1.0, the same upstream as the
`configobj-5.1.0.dev0` bundled in NVDA 2026.2). `nvda_stubs.loadConfigObj()` prefers an installed
version and falls back to this copy.

### Liveness and benchmarks

```powershell
python tests\probe_service.py
python tests\bench_http.py --repeat 5 --out tests\artifacts\bench_http.json
```

### Rebuild

```powershell
pwsh -NoProfile -File scripts\build_addon.ps1
```

**Artifact-directory rule: the build script only overwrites the same-name bundle.** It never deletes other files
in `dist/`; the public repository does not commit `dist/` and release packages are distributed through GitHub
Releases. `test_package.DistHygieneTests` only checks that no build temporaries are left in `dist/`.
The script only writes `dist/`, `tests/artifacts/` and `src/doc/*/readme.html` (via `make_readme_html.py`).
It **never installs the add-on and never touches NVDA's configuration**.

## 4. External service API contract

### Endpoints

| Method | Path | Notes |
|---|---|---|
| GET | `/health` | unauthenticated liveness, returns `{"logId","errorCode":0,"errorMsg":"Healthy"}` |
| GET | `/openapi.json` | unauthenticated OpenAPI document; `paths` lists the mounted endpoints (3.3.6 model-list fallback) |
| POST | `/ocr` | PP-OCRv6 fast profile, screen content → lines + boxes + scores (default endpoint) |
| POST | `/ocr-acc` | accurate profile (medium models); only mounted when the service runs with `-OcrAccurate` |
| POST | `/layout-parsing` | PaddleOCR-VL document parsing → layout blocks + Markdown; ~3 s for a small image, 7–13 s per page (optional endpoint) |

**Model list (3.3.6)**: this service has **no** `/models` endpoint and no `model` field in the request schemas — the
pipeline is chosen at start-up by the yaml. The client probes in order of convention: `GET /models` (OpenAI style,
`data[].{id, endpoint}`) → `GET /v1/models` → falls back to enumerating POST endpoints from `GET /openapi.json`.
The fallback uses endpoint paths as ids and the UI labels the source as "OpenAPI endpoint list" instead of pretending
it is a real model list.

### Authentication

Any one of (case-insensitive); the add-on uses the first:

```http
Authorization: Bearer <token>
X-API-Key: <token>
Token: <token>
```

`/health`, `/docs`, `/openapi.json`, `/redoc`, `/favicon.ico` are exempt. A wrong token yields **HTTP 401 + JSON**:

```json
{"logId":"xxx","errorCode":401,"errorMsg":"Unauthorized: missing or invalid token",
 "hint":"Set header  Authorization: Bearer <token>"}
```


**Fast token check (used by the 3.3.6 "test connection" button)**: the token middleware runs before routing, so a
request body **without `file`** (`{"fileType": 1}`) returns `401` for a bad token and `400/422` for a good one.
No inference is triggered, so it is fast even for PaddleOCR-VL.

### `/ocr` request

`Content-Type: application/json`; `file` is Base64 (an image URL also works).

| Field | Required | Sent by add-on | Meaning |
|---|---|---|---|
| `file` | yes | yes | Base64 image content |
| `fileType` | no | yes (`1`) | `1`=image, `0`=PDF |
| `visualize` | no | yes (`false`) | return the visualisation image; `ocrImage` is absent when false |
| `useDocOrientationClassify` | no | yes (`false`) | document orientation |
| `useDocUnwarping` | no | yes (`false`) | UVDoc unwarping (never for screenshots) |
| `useTextlineOrientation` | no | yes (`false`) | text-line orientation |
| `textDetLimitSideLen` | no | only if setting > 0 | detection longest side; smaller is faster (1280 was not faster here) |
| `textRecScoreThresh` | no | only if setting > 0 | server-side low-score filtering |
| `returnWordBox`, `logId` | no | no | unused |

The field set was verified against `paddlex__inference__serving__schemas__ocr__InferRequest` in `/openapi.json`.

### `/ocr` response (only what is used)

```jsonc
{
  "logId": "...", "errorCode": 0, "errorMsg": "...",
  "result": {
    "ocrResults": [
      {
        "prunedResult": {
          "rec_texts":  ["助力双方交往", ""],     // text; empty strings DO occur at low confidence
          "rec_scores": [0.998, 0.0],
          "rec_polys":  [[[x1,y1],[x2,y2],[x3,y3],[x4,y4]], ...],
          "rec_boxes":  [[x1,y1,x2,y2], ...],
          "textline_orientation_angles": [...],
          "model_settings": {...}
        },
        "ocrImage": "..."        // only when visualize=true
      }
    ]
  }
}
```

**Empty strings must be filtered.** A real 3840×2160 capture returned 126 entries including `""` with
`score=0.0`; leaving them in makes `" ".join(...)` produce runs of spaces, which then register as a
"text change" and trigger speech.

### `/layout-parsing` response (supported since 3.3.6)

```jsonc
{
  "result": {
    "layoutParsingResults": [
      {
        "prunedResult": {
          "parsing_res_list": [
            {"block_label": "text", "block_content": "body text", "block_order": 1, "block_bbox": [x1,y1,x2,y2]},
            {"block_label": "table", "block_content": "<table>…</table>", "block_order": 2}
          ]
        },
        "markdown": {"text": "# title\n…", "images": {...}}
      }
    ]
  }
}
```

The add-on only takes **text**: blocks are sorted by `block_order`, `figure/image/chart/seal` and markup-looking
content (`<table`/`<html`/`<img`/`![…]`) are skipped, and `score` is set to `1.0` (VL has no per-line confidence, so
`scoreThresh` cannot accidentally drop everything). If there are no usable blocks it falls back to splitting
`markdown.text` by lines. A blank page (`parsing_res_list` empty) returns `code 100, data []`, not an error.

### Error semantics and code mapping

| Situation | `code` | Text logged/spoken |
|---|---|---|
| success | `100` | `data` = `[{"text","score"}, ...]` |
| empty/invalid address | `901` | `OCR service address is empty or invalid.` |
| refused / DNS / other OSError | `902` | `Connection failed: <reason>` |
| timeout (incl. `URLError("timed out")`) | `903` | `Request timed out after <n> seconds` |
| HTTP 4xx/5xx (401/400/422/500) | `904` | `Service responded with HTTP status code: <n>. <errorMsg/hint>` |
| response is not JSON | `905` | `Invalid JSON response: <reason>` |
| response `errorCode != 0` | `904` | server `errorMsg` + `hint` |
| response has no `ocrResults` | `905` | `No OCR result found in the response.` |

When `code != 100` the loop calls `reportFailure(label, regionId=...)`: **each area announces only its first
consecutive failure** (gated by `notifyOnError`) and logs the rest; one success resets that area's flag.

## 5. Code tour

### `dedup.py` (new in 3.3.3: four deduplication strategies, zero NVDA dependencies)

```
MODES = ("classic", "commit", "incremental", "off")     also the drop-down order
DEFAULT_HOLD_MS=1500 / HOLD_MIN_MS=500 / HOLD_MAX_MS=10000
MAX_PENDING_SECONDS=10.0                                safety release when text never settles
BOUNDARY_CHARS                                          punctuation only, never whitespace
SpeechEvent(text, reason)                               reason is logged and asserted in tests
DedupEngine
    observe(regionId, text, mode, holdMs, now, threshold) -> [SpeechEvent]
    reset() / resetRegion(id) / prune(configuredRegions)
    stateFor(regionId)                                  read-only snapshot for tests
```

**Design notes**

- **It never reads the clock.** `now` is passed in by the caller (the plugin uses `time.monotonic()`, the tests
  use virtual time), so the state machine is fully reproducible and all 49 cases run in milliseconds.
- **No translation initialisation and no NVDA imports** — deliberate; a test guards against reintroducing
  `import addonHandler`.
- **The classic branch is identical to 3.3.2**: speak only when
  `similarityRatio(lastSpoken, text) < threshold`.
- **The commit branch** records `holdSince` (when the text last changed) and speaks `text[lastSpoken:]` once
  `now - holdSince >= holdMs` and this segment has not been released yet — so a prefix extension only reads the
  new part.
- **The incremental branch** looks for a punctuation boundary and reads up to it, advancing the watermark; with no
  boundary it falls back to the "stable" release.
- **Replacement detection**: when the new text no longer starts with `lastSpoken` it is a different line, so
  `lastSpoken` and `watermark` reset — otherwise a new line would be swallowed as a continuation.
- **Forced release**: if nothing has been spoken for 10 s (`reason="forced"`), release once, so a scrolling or
  animated area cannot silence the feature entirely.

### Typewriter timeline (virtual time, from `test_dedup.CommitModeTests`)

| Frame | Input | classic | commit (1500 ms) |
|---|---|---|---|
| t=1 | 你 | speaks "你" | silent |
| t=2 | 你好 | skipped | silent |
| t=3 | 你好冒险者 | speaks "你好冒险者" | silent |
| t=4 | 你好冒险者 (unchanged) | skipped | speaks "你好冒险者" |
| t=5 | 你好冒险者 (unchanged) | skipped | silent (segment already released) |

> Conclusion: `classic` emits fragments and never the complete sentence; `commit` speaks the whole line once it
> stops changing.

### `regions.py` (the multi-area core, zero NVDA dependencies)

```
NAVIGATOR_OBJECT=0 / WHOLE_SCREEN=1 / CURRENT_WINDOW=2 / CURRENT_CONTROL=3   # same values as the original target
TARGET_LABELS / TARGETS                   target names (English msgids) and legal values
DEFAULT_REGION                            whole screen, no crop, enabled
clampPercent / clampTarget                single-value normalisation
normalizeRegion(raw, index)               tolerant normalisation: missing fields, wrong types, out of range, long names
normalizeRegions(items)                   list normalisation + unique ids
loadFromJson(text)                        tolerant parsing; `[]` means "the user really has no areas"
toJson(regions)                           compact JSON (keeps CJK readable)
defaultName(region, index)                falls back to "Area N"
clippedRect(base, crop, minSide, screen)  four-way percentage crop (same maths as the original cropRectLTWH)
regionRect(region, resolveBase, screen)   injected base resolution → the actual capture rectangle
migrateFromLegacy(conf)                   old target+crop keys → one area
pruneState(state, regions)                drop dedup/error state of removed areas
describe(region, index)                   list row text: name | target | top x left x right x bottom x
```

Geometry, unchanged from the original: `left = base.left + int(base.width * cropLeft / 100)`, the size is what
remains after cropping, the result is clamped by `resX - left` / `resY - top`, and the minimum side is 10 px.
A further guarantee: `cropLeft + cropRight ≤ 100` and `cropUp + cropDown ≤ 100` (excess trims right/bottom).

### `__init__.py`

- Top: `ctypes` sets DPI awareness (physical pixels) and resolves `resX/resY`; on failure it degrades to `0, 0`.
- `confspec` (13 entries) + `config.conf.spec[CONFIG_SECTION]`; the old `target` and four crop keys are gone.
- `similarityRatio(previous, current)`: similarity with explicit `autojunk=False` (decision 8 above).
- `ensureRegionsMigrated()`: migrates **only when the config has no `regions` key**, so a user who deleted every
  area in the dialog (written as `[]`) does not get an area resurrected.
- `makeOcrClient()`: settings → client.
- `resolveBaseRect(target)`: returns a plain dict (not `RectLTWH`) to keep `regions` decoupled from NVDA types.
- `getRegionRects()`: `[(index, region, rect), ...]`, checked areas only.
- `scanRegions()` → `(changedTexts, failures)`: per area, its own client, capture → encode → OCR; an exception or
  failure in one area never affects the others; results are ordered, filtered and deduplicated.
- `speakTexts(texts)`: `queueSpeech` true → each text with `Spri.NEXT`; false → only the last with `Spri.NOW`.
- `OcrScreen()`: `scanRegions()` → per-area failure announcements → `speakTexts()`.
- `terminate()`: `active=False` plus `speech.cancelSpeech()` to flush queued speech.

### `lionGui.py`

- `LIONSettingsPanel`: external service settings + **area list** + queue-speech check box + recognition settings.
- The area list uses `nvdaControls.AutoWidthColumnCheckListCtrl` (shipped by NVDA, accessible, Space toggles the
  check box, notifies through `wx.EVT_CHECKLISTBOX`, `CheckedItems` / `CheckItem` read and write state).
- Buttons: Enable all / Disable all / Add… / Edit… / Remove / Move up / Move down; `EVT_LIST_ITEM_ACTIVATED`
  also opens the editor.
- `RegionEditDialog`: name, target (Choice), four crop spin controls; `onOk()` validates
  "top+bottom ≤ 100, left+right ≤ 100" and **keeps the dialog open** with a message box when invalid.
- `getRegions()` syncs the check boxes into the model before saving; `onSave()` writes the `regions` JSON and
  `queueSpeech`.

## 6. Settings and measurements

### Settings (14 entries)

| Key | Type | Default | Notes |
|---|---|---|---|
| `serviceUrl` | string | `http://127.0.0.1:23353` | **service base address** (may include a reverse-proxy path, no endpoint) |
| `model` | string | `/ocr` | **model / OCR endpoint**: endpoint path or full URL, combined with `serviceUrl` |
| `serviceToken` | string | `""` | empty means no authorization header |
| `requestTimeout` | float | `15.0` | seconds, 1.0–120.0 |
| `imageFormat` | int | `1` (JPEG) | 0 = PNG |
| `visualize` | bool | `false` | enabling it is a large slowdown |
| `scoreThresh` | float | `0.0` | client-side second filter (the server also has `textRecScoreThresh`) |
| `detLimitSideLen` | int | `0` | 0 = do not send |
| `notifyOnError` | bool | `true` | announce a failure only once per area |
| `regions` | string | `'[]'` | JSON area list (below) |
| `queueSpeech` | bool | `false` | true = queued, non-interrupting; false = interrupting |
| `dedup` | string | `"classic"` | per-area strategy: `classic` / `commit` / `incremental` / `off` (stored inside the area object) |
| `dedupHoldMs` | int | `1500` | "stable" threshold in ms, 500–10000; only used by `commit`/`incremental` |
| `threshold` | float | `0.5` | similarity threshold, compared per area |
| `interval` | float | `1.0` | seconds, 0.0–10.0 |
| `regexFilters` | string | `""` | regex filtering, applied to every area |

**First-start migration (3.3.6)**: if the config has no `model` key, the old full `serviceUrl` is split into
`(base, model)`. `splitServiceUrl()` guarantees that "split then compose" equals 3.3.5's `normalizeBaseUrl()` result
(8 round-trip cases lock this), so upgrading users keep exactly the same request URL.

Area object fields:

```jsonc
{"id": 1, "name": "字幕", "enabled": true, "target": 1,
 "cropUp": 70, "cropDown": 0, "cropLeft": 0, "cropRight": 0,
 "dedup": "classic", "dedupHoldMs": 1500}
```

### End-to-end latency (`tests/artifacts/bench_http.json`, 3840×2160, incl. base64 and HTTP round trip)

| Area | Format | min | **p50** | max | lines |
|---|---|---|---|---|---|
| full 3840×2160 | JPEG | 294.4 | **301.6** | 307.8 | 87 |
| full 3840×2160 | PNG | 446.8 | **472.1** | 1131.2 | 87 |
| 1920×1080 | JPEG | 133.9 | **141.9** | 148.5 | 50 |
| 1920×1080 | PNG | 164.5 | 166.4 | 180.7 | 51 |
| subtitle 3840×540 | JPEG | 75.2 | **79.3** | 92.5 | 22 |
| subtitle 3840×540 | PNG | 101.9 | 102.9 | 104.5 | 23 |
| subtitle 3840×259 | JPEG | 28.0 | **29.7** | 44.3 | 7 |
| subtitle 3840×259 | PNG | 39.9 | 41.7 | 56.3 | 4 |

**With several areas the total is roughly the sum of the parts** (one request in flight at a time). At the
default one-second interval, a "subtitle strip" plus a "chat" area costs about 100–150 ms — plenty of headroom.

## 7. Test system

| File | Cases | Coverage |
|---|---|---|
| `test_ocr_client.py` | 66 | URL normalisation/composition/migration split, payload, response parsing (incl. PaddleOCR-VL layout blocks), three-tier model discovery, token verification, all stub-server error paths, `testConnection`, 4 live-service cases |
| `test_regions.py` | 43 | JSON round trip and tolerance, normalisation and bounds, four-way crop geometry, base-resolution fallbacks, legacy migration, state pruning, list row text, **dedup field normalisation** |
| `test_dedup.py` | 102 | **the four dedup strategies** plus 3.3.5 regressions: typewriter whole-line, stability window, clear-then-repeat, line-by-line append, whole-line replacement, truncated tail, 10 s forced release, punctuation boundaries, English words not split, state lifecycle (virtual time), incremental-jitter regressions, fuzzy anchor, punctuation-only filter, digit changes |
| `test_lite_variant.py` | 17 | lite edition: manifest identity, localised-manifest parsing, isolated config section, 17 settings, no client copy, staged client byte-identical to the full edition, model composition, concise wording, translation coverage, bundle checks |
| `test_plugin_integration.py` | 71 | config-section independence; area migration and **service-address migration (3.3.6)**; only checked areas captured; per-area crop and target; **different strategies per area**; per-area dedup and failure isolation; queue/non-queue priorities and order; state cleanup after edit/remove; regex filtering and whitespace collapsing; toggle script; loop exit; **1 live end-to-end case** |
| `test_manifest_and_config.py` | 32 | manifest fields and apiVersion; **original description/log first, fork below**; confspec defaults, out-of-range, booleans, JSON-with-commas not split; source hygiene (no engine/PIL/bytecode remnants, `regions.py` free of GUI imports, `dedup.py` free of NVDA imports, translatable strings, .po contents) |
| `test_package.py` | 17 | artifact naming, forbidden entries, required entries (incl. `regions.py`/`dedup.py`), no original override, size budget, doc references, **readme original sections before the fork section**, **manifest original block first**, translation catalog, artifact matches `src/` file-for-file |

**348 cases total**, all runnable with a single `unittest discover`.

**How the plugin is tested without NVDA**: `test_plugin_integration.py` replaces
`gui`/`wx`/`api`/`ui`/`tones`/`speech`/`scriptHandler`/`globalPluginHandler`/`screenCurtain`/`locationHelper`/`PIL`
with `types.ModuleType`/`MagicMock` stubs and loads `__init__.py` as the `lion` package via
`importlib.util.spec_from_file_location`. `ui.message` becomes a collector that also records
`speechPriority`, so "what is spoken, at which priority, in which order" is directly assertable.

**Two traps in the live end-to-end case** (documented in the code):
1. Every other case feeds `FakeImage`, which the server rejects with **422 Invalid input file** — the live case
   must swap in a real screenshot.
2. `PIL` is already stubbed, so Pillow's JPEG save does `import PIL.JpegImagePlugin` and fails (visible as
   `KeyError: 'JPEG'`). `PIL.Image`/`ImageFile`/`JpegImagePlugin`/`PngImagePlugin` must therefore be captured
   *before* the stub is installed and put back for that case.

## 8. Defects fixed / limitations removed

### 8.1 Regex filtering left stray whitespace (fixed in 3.3.1)

Original: `all_text = " ".join(...)` and only then `re.sub`. Line anchors matched nothing, and even a successful
match left extra spaces (`"广告 正文"` → `" 正文"`), which was written back to `prevString` and could make the next,
cleaner text look "changed" — or speak empty content. `filterText()` now collapses whitespace after filtering.

### 8.2 `autojunk` risk in similarity dedup (hardened in 3.3.2)

`difflib.SequenceMatcher` defaults to `autojunk=True`: for sequences of length ≥ 200 it ignores elements that
appear more than 1% of the time. CJK subtitles repeat common characters heavily, so long lines risked being
judged "identical" and never spoken again. `similarityRatio()` now passes `autojunk=False`, and 6 cases lock the
dedup semantics (including the threshold 0 and 1 boundaries).

### 8.3 Structural limits of the single-area era (solved in 3.3.2)

- one area only → now several, each with a check box;
- no way to hear several areas → queued speech;
- one global `prevString` → independent per area;
- one failure affecting the whole round → isolated per area.

## 8. 3.3.4 fixes: OCR-jitter duplicates and whole-text re-reads

### 8.1 Bug 1: commit re-announced the same sentence

3.3.3 used an exact whole-string prefix test and overloaded `lastSpoken` as both "what was spoken" and
"the anchor":

```
if state.lastSpoken and not text.startswith(state.lastSpoken):
    state.lastSpoken = ""        # one frame of jitter wipes the memory
    state.watermark = 0
```

Real OCR differs by one character almost every frame, so:

```
t=3  你好冒险者欢迎来到新手村  -> spoken
t=4  你好冒险者欢迎来到新手材  -> prefix mismatch -> record wiped
t=5  你好冒险者欢迎来到新手村  -> hold restarts -> spoken again (duplicate)
```

### 8.2 Bug 2: incremental re-read from the beginning

`watermark` was a *position* anchor, zeroed by the same branch whenever a frame did not extend the
previous frame verbatim. Once it was zero, `findBoundary(text, 0)` returned the last punctuation mark
in the whole box, so speech restarted at the first character.

### 8.3 Bug 3: the 10-second guard cut normal typing

`pendingSince` was refreshed on every change, so a steadily growing line never aged it; at 10 seconds
`forced` fired and spoke half a sentence.

### 8.4 The fix (dedup.py in 3.3.4)

1. **Frame classification** `classifyChange(prev, cur)` → `same / empty / growth / jitter / replace`
   * `growth`: `cur.startswith(prev)` — the typewriter is still appending
   * `jitter`: whitespace-insensitive edit distance ≤ 1, common prefix ≥ 3 chars and ≥ 50% of the
     length (real bounded Levenshtein DP)
   * a difference only in a trailing ASCII digit/letter is `replace` (`frame 1` → `frame 2` is new content)
2. **Jitter never wipes memory**: `jitter`/`same` frames only update `lastSeen`; they do not touch
   `holdSince`, `announced`, and are never used as the spoken text.
3. **Speech uses `stableText`** (the last substantive frame), so jitter content is never spoken.
4. **Content anchor**: `announced` holds the prefix that was actually spoken; incremental computes its
   start with `commonPrefixSpoken(announced, text)` instead of a positional watermark.
5. **`_unspokenTail` ordering**: check truncation / same-length jitter first (return nothing), then
   prefix extension (return the new part), then replacement.
6. **Empty-frame grace**: state resets only after `max(2s, 2×hold)` of consecutive empty frames.
7. **Guard timer** `unstableSince`: reset on growth/replace, not on jitter — normal typing can never be
   cut, while a static-animation box still gets one forced announcement eventually.
8. **Rebuild state on strategy switch**, so a stale `released=True` from `classic` cannot mute `commit`.

### 8.5 Constants

| Constant | Value | Purpose |
|---|---|---|
| `JITTER_EDIT_DISTANCE` | 1 | max edit distance for "same sentence, jittered" |
| `JITTER_MIN_PREFIX` | 3 | min common prefix for jitter |
| `JITTER_MIN_PREFIX_RATIO` | 0.5 | min common-prefix ratio for jitter |
| `ALREADY_ANNOUNCED_RATIO` | 0.90 | fuzzy "we just said this" |
| `EMPTY_GRACE_SECONDS` | 2.0 | grace before an empty frame resets state |
| `MAX_PENDING_SECONDS` | 10.0 | safety release when nothing progresses |
| `MIN_CHUNK_CHARS` | 2 | minimum incremental chunk |
| `PREFIX_CAP` | 160 | prefix comparison scan cap |

### 8.6 New regression tests (`tests/test_dedup.py`)

`OcrJitterRegressionTests`, `BoundaryRegressionTests`, `ChangeClassifierTests`, `EmptyGraceTests`,
`ModeSwitchTests`: single-char misread, trailing punctuation flicker, lost space, one empty frame,
two variants alternating for 60 frames, "a jitter frame is never spoken", incremental never repeating a
prefix, one announcement per sentence, opening brackets are not boundaries, digit changes are new content.

### 8.7 3.3.5: incremental duplicates after stabilisation

After 3.3.4 the `commit` strategy was stable, but `incremental` still re-read the whole passage once the
sentence settled. Two compounding causes:

1. **The jitter test used a common-prefix ratio** (`classifyChange`:
   `prefix / length >= JITTER_MIN_PREFIX_RATIO`). A misread character near the start of a long line gives
   `3/26 = 0.12`, so the frame was classified as `replace`.
2. **`incremental` cleared `announced` on `replace`**, so `findBoundary(source, 0)` hit the first
   punctuation mark and speech restarted at character 0. `commit` already had the
   `ALREADY_ANNOUNCED_RATIO` guard, which is why only the incremental mode showed the bug.

Fix (`dedup.py`):

* `classifyChange` now uses a **length-aware edit distance**:
  `JITTER_EDIT_DISTANCE=1` for short lines, `JITTER_EDIT_DISTANCE_LONG=2` for lines of at least
  `JITTER_LONG_TEXT_LENGTH=12` characters (with `similarityRatio >= JITTER_LONG_MIN_SIMILARITY=0.8`);
  `shortSide < JITTER_MIN_LENGTH=3` is always `replace` (so choices such as `是`/`否` are not swallowed).
* New `differenceSnippet(a, b)` strips the common prefix/suffix; a difference containing an **ASCII digit**
  becomes `replace` (`frame 1`→`frame 2`, `HP: 100`→`HP: 90` are state updates).
* `commonPrefixSpoken` gained a third "**fuzzy anchor**" layer: when exact prefix and position tolerance
  both fail, a `SequenceMatcher` pass is used and, if coverage reaches `ANCHOR_MATCH_RATIO=0.85`, the end
  of the matched blocks becomes the resume point. Only for `len(spoken) >= ANCHOR_MIN_LENGTH=12`
  (short strings lack redundancy and would anchor incorrectly).
* `_incremental` now has the same `similarityRatio(announcedFull, source) >= ALREADY_ANNOUNCED_RATIO`
  guard as `commit`.
* New `hasSpeakableContent()`: punctuation-only fragments are not spoken on their own (the anchor still
  advances, so they are not retried).

New constants:

| Constant | Value | Purpose |
|---|---|---|
| `JITTER_EDIT_DISTANCE` / `JITTER_EDIT_DISTANCE_LONG` | 1 / 2 | jitter tolerance for short/long lines |
| `JITTER_LONG_TEXT_LENGTH` | 12 | "long text" threshold |
| `JITTER_LONG_MIN_SIMILARITY` | 0.8 | similarity floor when 2 edits are allowed |
| `JITTER_MIN_LENGTH` | 3 | too-short strings are never jitter |
| `ANCHOR_MATCH_RATIO` / `ANCHOR_MIN_LENGTH` | 0.85 / 12 | fuzzy anchor thresholds |

New regression tests (`tests/test_dedup.py`, 77 → 102): `IncrementalJitterRegressionTests` (early/mid/two-char
jitter, alternating variants, truncation, multi-sentence box, genuine new sentence, digit change,
punctuation-only, property assertion), `AnchorTests`, `SpeakableChunkTests`, `DigitDifferenceTests`.

## 9. 3.3.6: custom model (OCR endpoint) and model-list discovery

### 9.1 Why "model" means endpoint

PaddleX serving is "one pipeline per app": the pipeline/model is chosen **at start-up** by the yaml, and neither
`/ocr` nor `/layout-parsing` has a `model` field in its request schema. PaddleX 3.7.2's serving layer only registers
`/health` — there is no `/models` endpoint. The only dimension a client can switch is therefore **which endpoint to
POST to**, which this add-on calls `model`.

`serviceUrl` consequently became a **service base address**:

```ini
serviceUrl = http://127.0.0.1:23353   ; base (a reverse-proxy prefix such as http://host/paddle is allowed)
model      = /ocr                     ; endpoint path, or a full URL (the base is then ignored)
```

Request URL = `composeUrl(serviceUrl, model)`: a full-URL model is used as-is; otherwise `base + "/" + model`;
an empty model falls back to 3.3.5's `normalizeBaseUrl()` behaviour (append `/ocr` when there is no path).

**Migration**: `ensureServiceMigrated()` runs only when the config has no `model` key and splits the old full address
with `splitServiceUrl()`. `splitServiceUrl()` + `composeUrl()` reproduce the old `normalizeBaseUrl()` result exactly
(8 round-trip cases lock this, including a `/paddle/ocr` proxy prefix).

### 9.2 Three-tier model discovery

`HttpOcrClient.listModels()` probes in order of convention and returns `(models, source, error)`:

| Tier | Request | `source` | Notes |
|---|---|---|---|
| 1 | `GET /models` | `models` | OpenAI style; an item may name its real endpoint via `endpoint`/`path`/`url` |
| 2 | `GET /v1/models` | `v1/models` | OpenAI-compatible implementations |
| 3 | `GET /openapi.json` | `openapi` | **fallback**: `paths` entries with `post`, excluding `/restructure-pages` |

`parseModelList()` tolerates `data` and `models` list keys and `id`/`name`/`model` identifier keys. Entries without a
routable endpoint are dropped (Ollama's `/api/tags` is such a shape), because the add-on must know where to POST.
When `source == "openapi"` the settings panel explicitly says "List source: OpenAPI endpoint list" instead of pretending
it is a real model list.

### 9.3 Merged button: test connection + get model list

`lionGui.onTestAndDiscover()` does the following in one click:

1. `listModels()` → refresh the editable combo box (it shows model ids and maps them to request endpoints internally);
2. on failure call `testConnection()` to distinguish "service unreachable" from "service has no model list";
3. `verifyToken()`: POST `{"fileType": 1}` (missing `file`) — 401 means a bad token, 400/422 means a good one, and no
   inference is triggered;
4. for endpoints other than `/layout-parsing`, send an 8×8 white image as a recognition test; skip it for VL and say so
   (a single VL request takes 3 s or more).

`onSave` stores the **request endpoint** (mapped through `modelEndpoints`), so the config semantics stay stable; any
hand-typed path or full URL is stored verbatim.

### 9.4 Text extraction for PaddleOCR-VL

`parseResult()` decides by response shape:

1. `prunedResult.rec_texts` present → PP-OCRv6 path (existing behaviour: drop empty strings and `scoreThresh`);
2. `prunedResult.parsing_res_list` present → sort by `block_order`, take `block_content`, skip
   `figure/image/chart/seal` and content starting with `<table`/`<html`/`<img`/`![`, set `score = 1.0`;
3. no usable blocks → split `markdown.text` by lines;
4. entries exist but carry no text → `100/[]` (a blank screen is not an error); neither structure → `905`.

### 9.5 Files touched

- `ocrClient.py`: `normalizeServiceBase` / `normalizeModel` / `composeUrl` / `splitServiceUrl` /
  `parseModelList` / `parseOpenApiModels` / `openapiUrl` / `_getJson`; `HttpOcrClient(..., model=)`, `listModels()`,
  `verifyToken()`, the VL branch of `parseResult()`.
- `__init__.py`: `model` added to `confspec` (14 entries), `serviceUrl` default is now a base address,
  `ensureServiceMigrated()`, `makeOcrClient()` passes `model`.
- `lionGui.py`: editable `wx.ComboBox`, merged button `onTestAndDiscover`, `setModelChoices` / `modelTarget`.
- **Unchanged**: `dedup.py` (byte-identical to the 3.3.5 bundle), `regions.py`, the locally deployed server.

### 9.6 Explicit non-goals

- No server-side changes, no `/models` addition, no server patch: the `/models` probe is reserved for other/future
  compatible services.
- No per-pipeline feature work (layout, tables, formulas, charts, seals); only the common "text lines" capability.
- `detLimitSideLen` / `scoreThresh` / `visualize` have no effect on `/layout-parsing` (the server ignores them);
  the settings stay unchanged and this limitation is documented.

## 10. 3.3.7: shorter UI wording and the lite edition

### 10.1 Shorter wording (full edition)

Only strings in `lionGui.py` changed (behaviour, configuration keys and de-duplication are untouched), aiming at the
original add-on's concise style:

- strategy labels drop the long parentheses: `Classic (similarity)` / `Read once when stable` / `Read as it grows` /
  `Off (every change)`;
- shorter setting labels: `Stable after (ms):`, `&Queue speech`, `Service &token:`, `&Model / endpoint:`,
  `Return &visualisation image (slow)`, `Detection longest side (0 = default):`;
- the button is now `&Test / get models` (was "Test connection and get model list");
- shorter status messages: `Source: OpenAPI endpoints`, `Connected; model list unavailable: %s`,
  `Recognition test skipped (PaddleOCR-VL is slow).`.

The `.po` msgid/msgstr pairs were replaced and the `.mo` rebuilt; string assertions in `test_manifest_and_config`
and `test_package` were updated accordingly.

### 10.2 Lite edition (new parallel add-on)

Sources live in `variants/lite/src/` and are based on the single-area, near-original UI of 3.3.1:

| Item | Value |
|---|---|
| Add-on ID / summary | `LION-modified-lite` / "LION 修改版 lite" |
| Internal package | `globalPlugins/lionlite` (must differ from `lion`, otherwise NVDA's namespace collides) |
| Configuration section | `LION-modified-lite` (isolated from `LION-modified` and `lion`) |
| Settings | 17 = the 16 keys of 3.3.1 + `model` (default `/ocr`); `serviceUrl` defaults to `http://127.0.0.1:23353` |
| Client | the **same source file** as the full edition: `src/globalPlugins/lion/ocrClient.py`; no copy in lite |
| Behaviour | one area (target + four-way crop), classic similarity de-duplication, no queue, no per-area strategies |
| UI | 3.3.1 controls + model/endpoint combo box + "Test / get models" button, concise labels |

Build with `scripts/build_addon.ps1 -Variant lite`: it copies the full client into a temporary staging
(`build/lite/globalPlugins/lionlite/ocrClient.py`) before packaging, producing
`dist/LION-modified-lite-<version>.nvda-addon` and `tests/artifacts/build-report-lite.json`.
`make_readme_html.py` gained a `--src` option and reads the version from the manifest.

### 10.3 Tests

- `tests/test_lite_variant.py` (new): manifest identity, isolated config section, 17 settings, no client copy in the
  source tree, staged client byte-identical to the full edition, model composition, concise-wording assertions,
  translation coverage, bundled client identical to the full edition;
- `test_package.py`: the full bundle is looked up by exact filename (so the lite bundle is not mistaken for it);
  the dist-hygiene test only checks for build temporaries;
- string assertions in `test_manifest_and_config.py` / `test_package.py` use the new short strings.

### 10.4 Version scheme

The major version follows upstream **3.3** (original LION 3.3.0); fork releases use **3.3.N** and this release is
**3.3.7** for both add-ons. Public versions are 3.3.1–3.3.7; release packages are distributed through GitHub
Releases.

## 11. Coexistence with the original: mechanism and caveats

- **Unique add-on ID.** NVDA keys add-ons on the manifest `name` (`AddonBase.name` in
  [addonHandler](https://github.com/nvaccess/nvda/blob/release-2026.2/source/addonHandler/__init__.py));
  installation directories are derived from it, so nothing is overwritten.
- **Separate config section**: `LION-modified` vs `lion`.
- **Separate translations**: the `.mo` is loaded from the add-on's own path
  (`Addon.getTranslationsInstance`).
- **Distinguishable panels**: the category label is the panel class's own `title`
  (`SettingsPanel.title` in `gui/settingsDialogs.py`); on a Chinese UI they read "LION" and "LION 修改版".
- ⚠️ **Gesture conflict**: both bind `NVDA+Alt+N`. NVDA's gesture map is a global namespace and
  `scriptHandler._findScript` takes the **first** matching script, so which add-on fires is not predictable.
  The user must either clear the original's `NVDA+Alt+N` in Input gestures, or rebind one of them.
  **This must be stated in the readme and in the delivery notes.**

## 12. Extension points

1. **`/layout-parsing` (document parsing).** `ocrClient.parseResult` already tolerates
   `result.layoutParsingResults[0].prunedResult.rec_texts`. A real "page → Markdown" feature needs a method
   reading `markdown.text` and its own gesture (7–10 s per page rules out the 1 s loop).
2. **Visual area picking.** Areas are still specified by percentages; a helper that previews the resulting
   rectangle (or prefills from the current target) would make setup much easier.
3. **Per-area overrides.** `threshold` / `regexFilters` / `interval` are global today; optional fields in the
   area object would allow per-area values (`loadFromJson` is already an extensible dict structure).
4. **Connection reuse.** Each request opens a new TCP connection; irrelevant on loopback, worth revisiting for a
   remote service.
5. **Backoff.** Failures retry every `interval`; exponential backoff in `reportFailure` would quiet a dead service.
6. **Proxy handling.** `urllib` honours system proxy settings by default; set `no_proxy` if needed.

## 13. Known limitations and risks

| Limitation | Detail | Impact |
|---|---|---|
| External service required | no engine inside the add-on | totally unusable when the service is down (one announcement per area + logs) |
| Line order is detection order | `rec_texts` order, joined with spaces | no effect on subtitles; whole documents need `/layout-parsing` |
| More areas cost time linearly | one request in flight at a time | only check the areas you need |
| Stopping does not clear queued speech | already-queued texts finish playing | documented; disabling the add-on or quitting NVDA calls `cancelSpeech` |
| Not executed inside an NVDA process here | per the "code only, do not install" instruction | code paths are covered by 58 integration cases + static checks; **confirm panel and gesture after the first real install** |
| Gesture conflict | same default key | must be unbound manually (§9) |
| Token stored in clear text in `nvda.ini` | NVDA config is plain text anyway | mind the config directory permissions on shared machines |
| Service listens on `0.0.0.0` | LAN-reachable, token-only protection | the add-on can target a remote host, but consider the exposure |

## 14. Change checklist

1. Run `python -W ignore::ResourceWarning -m unittest discover -s tests -p 'test_*.py'` (with
   `PYTHONDONTWRITEBYTECODE=1`).
2. New UI string? Add it to `src/locale/zh_CN/LC_MESSAGES/nvda.po` and recompile the `.mo`:
   ```powershell
   python <python-env>\Tools\i18n\msgfmt.py `
     -o src\locale\zh_CN\LC_MESSAGES\nvda.mo src\locale\zh_CN\LC_MESSAGES\nvda.po
   ```
   (`test_package.BundleTranslationTests.testEveryPluginStringHasAnEntry` catches omissions.)
3. Changing a readme? Run `scripts\build_addon.ps1`, which regenerates the HTML.
4. **When editing docs or the manifest, preserve "original first, fork below"** — dedicated cases in
   `test_manifest_and_config` and `test_package` check the ordering.
5. Changing the area model or crop maths? Update `regions.py`, `test_regions.py`, and the expectations of the
   "checked areas only / per-area crop" cases in `test_plugin_integration`.
6. Changing a setting? Update `confspec`, the `lionGui.py` control, `onSave()`, the table in §6, and keep
   `ConfigSectionTests.testSpecHasEveryKey` accurate.
7. Bumping the version? Update `manifest.ini` `version` (it names the artifact) and
   `test_package.EXPECTED_VERSION`.
8. After packaging, record the SHA256 and size, and ship `tests/artifacts/build-report.json`.

## 15. Licence and credits

- Original LION: Stefan Moisei, Cary-rowen, anmi2004, hwf1324 (GPL v2 or later).
- This fork: **xiaoshunxing** — external PaddleOCR service integration, multi-area OCR, queued speech,
  settings panel, regex-filter defect fix, packaging slimming and the test system.
- Attribution appears in `manifest.ini` `author`, both readmes, and the changelog in
  `locale/zh_CN/manifest.ini`.
- Releases must stay GPL v2+ and must not remove the original authors.

AI disclosure: This text was created with AI participation.

Code disclosure: The modified code is based on the original LION and was written with AI participation.

Model version: DeepSeek-V4.1-Flash.
