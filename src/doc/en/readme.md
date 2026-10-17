# What can this add-on do?
LION is an add-on that performs automatic Optical Character Recognition (OCR) on specific screen areas at predefined intervals.  

Why is it called "smart"? Not because I developed it, nor because the "i" makes a clever acronym.  

Since it performs repeated OCR scans on the same screen area, it would normally read identical text multiple times - which isn't ideal. To solve this, I implemented a mechanism that prevents speech output when newly recognized text closely matches previous results.

# What can I use it for?
My primary purpose for developing this add-on was subtitle reading. Its working principle enables it to read various types of screen-based subtitles, including those on YouTube, Netflix, Bilibili, embedded subtitles in AVI files, and even live TV captions!  

When using, always set videos to full-screen mode as it mimics human visual perception. Larger text yields better recognition results, though accuracy isn't perfect. For optimal performance:  
- Enlarge subtitle fonts when possible  
- Use high-resolution displays  
The OCR engine isn't flawless and may struggle with certain graphics.  

Beyond subtitles, it can monitor screen text that isn't directly accessible, like video game menus. However, it cannot recognize highlighted text selections.

# How do I use it?
To start with default settings: Press <kbd>NVDA+ALT+N</kbd>. LION will perform full-screen OCR every 1 second and only speak when text changes.  

For customization:  
1. Navigate to <menupath>NVDA menu > Preferences > LION Settings</menupath>  
2. Example use case: Video files might display logos in the top-left corner that get read alongside subtitles. The next section explains solutions.  

**Available settings**:  
1. **OCR Interval**: Frequency of OCR operations (0.1-10 seconds)  
2. **OCR Target**: Screen area to scan (Options: Current Control/Current Window/Navigation Object/Full Screen)  
3. **Crop Pixels (Top/Bottom/Right/Left)**: Trims unwanted areas in Full Screen/Current Window modes. Useful for ignoring persistent logos - e.g., cropping 10% from top removes top-left logos. For efficiency, you might crop 70% from top as subtitles typically occupy the lower third.  

# Changelog
## Version 2.0
1. Complete OCR engine overhaul using PaddleOCR-json for improved accuracy  
2. Implemented add-on template for easier compilation  

## Version 1.15
1. Adapted for NVDA 2022.1 compatibility  
2. Completed interface translations  
3. Added shortcut customization in <menuitem>Input Gestures</menuitem> dialog  
4. Added warnings when initiating OCR during screensaver/black screen  
5. Changed default shortcut to <kbd>NVDA+ALT+N</kbd>  

## Version 1.12
1. Ensured compatibility with NVDA 2021.1  
2. Fixed various bugs  
3. Updated activation/deactivation sounds  

## Version 1.11
Fixed a major YouTube full-screen mode bug  

## Version 1.1
1. Crop settings now affect Current Window mode  
2. Added similarity threshold (0-1) for gaming scenarios:  
   - Compares current text with previous output  
   - 0: All texts considered identical (unusable)  
   - 1: Always speaks (even duplicates)  
   - Default: 0.5  

## Version 1.0
Initial release

--------

# Changes in this fork (LION modified 3.3.7)

## 3.3.7: shorter UI wording + the lite edition

### Shorter wording (full edition)

No behaviour changed in this release, only the wording went back to the **short labels of the original add-on**:

- de-duplication strategies no longer carry long parentheticals: `Classic (similarity)`, `Read once when stable`,
  `Read as it grows`, `Off (every change)`;
- labels are shorter, e.g. `Stable after (ms):`, `&Queue speech`, `Service &token:`, `&Model / endpoint:`,
  `Return &visualisation image (slow)`, `Detection longest side (0 = default):`;
- "Test / get models" is now **"Test / get models"**, and status messages are shorter
  (e.g. "Source: OpenAPI endpoints").

Behaviour, configuration keys and de-duplication are identical to 3.3.6.

### Lite edition (new parallel add-on)

A separate add-on **LION modified lite** (ID `LION-modified-lite`) is released alongside:

- based on the **single-area, near-original UI** of 3.3.1, with fewer settings;
- **same client as the full 3.3.7**: model/endpoint selection (`/ocr`, `/ocr-acc`, `/layout-parsing` or custom),
  the "Test / get models" button and PaddleOCR-VL text extraction;
- keeps 3.3.1 behaviour: one OCR area, classic similarity de-duplication, no queued speech, no per-area strategies.

The two add-ons have different IDs, package names and configuration sections, so they can be installed **together**
(the default gesture is shared, so rebind one of them). See the lite add-on's own documentation for details.

### Version numbering

The major version follows upstream **3.3** (original LION 3.3.0); fork releases use **3.3.N** and this release is
**3.3.7**. Release packages are distributed through GitHub Releases.

## 3.3.6: custom model (OCR endpoint) and one-click test / model list

### What "model" means here

The PaddleOCR service hosts **several pipelines on one port**: `/ocr` (PP-OCRv6, fast), `/ocr-acc` (accurate, only if
the server enables it) and `/layout-parsing` (PaddleOCR-VL). The server has **no model parameter**: the pipeline is
chosen at start-up, so the client can only choose **which endpoint to POST to**. In this add-on "model" therefore means
**OCR endpoint**: `/ocr` by default, or `/ocr-acc`, `/layout-parsing`, any custom path, or even a full URL pointing at a
service on another machine.

> The service URL is the base address (e.g. `http://127.0.0.1:23353`) and the model is the endpoint (e.g. `/ocr`); the
> request URL is the two joined together. **Configurations from 3.3.5 are migrated automatically** on first start,
> producing exactly the same request URL as before.

### What the "Test / get models" button does

One button performs four steps and reports each of them below the button:

1. **Health check** on `/health`.
2. **Model list**, tried in order of how conventional the API is:
   - `GET /models` — OpenAI-style model list (the `endpoint` field, when present, maps a model id to the real endpoint);
   - `GET /v1/models` — OpenAI-compatible implementations;
   - `GET /openapi.json` — **fallback**: the bundled PaddleOCR service has no `/models`, so this tier is used and the UI
     states "List source: OpenAPI endpoint list" instead of pretending it is a real model list.
3. **Token check**: a request body without `file` returns 401 for a bad token and 400/422 for a good one — no inference
   is triggered, so it is fast even for PaddleOCR-VL.
4. **Recognition test**: an 8×8 white image is sent to fast endpoints. For `/layout-parsing` this step is skipped with a
   note, because PaddleOCR-VL needs several seconds per request and would otherwise look like a timeout.

The combo box is editable: any endpoint that is not in the list can be typed in.

### Notes for PaddleOCR-VL (`/layout-parsing`)

- It is a document parser and is an order of magnitude slower than PP-OCRv6; raise the request timeout (the default 15 s
  may be too short, the maximum is 120 s).
- Only text is used (in layout-block order); tables, formulas, charts and seals are skipped.
- "Detection longest side", "minimum recognition score" and similar settings have no effect on `/layout-parsing`
  (the server ignores them); leave them at their defaults.

## What 3.3.5 fixes (reported from a real game)

After 3.3.4, "read once when the text stops changing" was stable, but "read as it grows" announced the
whole text again **after the sentence had settled**. 3.3.5 fixes that.

**Two causes, compounding:**

1. The frame classifier used a *common-prefix ratio*. In a long line, a misread character near the
   **beginning** (e.g. position 3) makes that ratio tiny (3/26), so the frame was classified as
   "a new sentence".
2. On "new sentence", `read as it grows` cleared its record of what had been spoken and therefore
   **restarted from the first character**. ("Read once when stable" already had an
   "already announced" guard, which is why only the incremental mode showed the bug.)

**What 3.3.5 changes:**

- The jitter test is now a **length-aware edit distance**: one difference for short lines, up to two
  for long lines (12+ characters). The prefix ratio is gone.
- A **fuzzy anchor** locates the resume point even when the already-spoken text contains a misread
  character, so only the new part is read.
- Incremental mode now has the same **"already announced" similarity guard** as the stable mode.
- Punctuation-only fragments (e.g. a stray `！`) are no longer spoken on their own.
- Digit/counter changes (`frame 1` → `frame 2`, `HP: 100` → `HP: 90`) are explicitly treated as new
  content and are never swallowed as jitter.

**Expected behaviour after the fix**: long dialogue is read phrase by phrase; once it finishes and sits
on screen, an occasional OCR misread no longer causes the whole passage to be read again.

## What 3.3.4 fixes (reported from a real game)

Two real-world problems with the 3.3.3 strategies are fixed in 3.3.4:

1. **"Read once when stable" re-announced the same sentence repeatedly.**
   Real OCR jitters every frame (a missing punctuation mark, an extra space, one misread character,
   occasionally a completely empty frame). The old implementation treated any difference as
   "a new sentence", wiping the record of what had already been spoken, so the line was announced
   again once it settled. Frames are now classified as *same / jitter / growth / replace*, and
   **jitter never clears the announced record** — so a sentence is spoken once.

2. **"Read as it grows" re-read from the beginning.**
   The old anchor was "the whole previous frame"; one frame of jitter reset it to zero and speech
   restarted at the first character. The anchor is now **the content that was actually spoken**, so
   only the new fragment is read.

3. Also fixed: a long line typed character by character for more than 10 seconds could be cut by the
   safety guard, speaking **half a sentence**. The guard now only fires when the content really stops
   growing.

### When repetition is still expected

* **The dialogue box is really cleared and the same line appears again** (repeated shop dialogue):
  spoken again by design.
* **Two regions cover the same text**: each region deduplicates on its own, so it is read twice.
  Keep only one region.
* **A line is replaced by a completely different one and then restored** within a short time:
  treated as two sentences.
* **A change of a single digit** (e.g. `frame 1` → `frame 2`) is treated as new content, so it is
  never swallowed by the jitter logic.

## What is new compared with 3.3.2 (read this first)

Every OCR area now has a **deduplication** drop-down. The default is still "Classic (similarity)", so
**subtitle usage is exactly as before**; for RPG dialogue, switch the dialogue area to
"Read once when stable".

| Deduplication | Best for | Behaviour |
|---|---|---|
| Classic (similarity) **default** | Subtitles, text that appears in one go | speaks when similarity to the previous text is below the threshold (original behaviour) |
| Read once when stable | **RPG dialogue, typewriter effects** | waits until the text stops growing, then speaks the whole line once |
| Read as it grows | Very long dialogue (10 s or more) | releases phrase by phrase at punctuation while it is still being typed |
| Off | Troubleshooting | speaks every change |

## Why a new strategy is needed

The original dedup assumes the text appears complete in one frame (subtitles). RPG dialogue appears **one
character at a time**, so every frame is an extension of the previous one and the similarity keeps climbing. After
speaking "你" from the first frame, the whole line is judged "unchanged" and never spoken again.

Measured on this machine with the default threshold of 0.5:

| Frame | Recognised text | Similarity to previous | Classic strategy |
|---|---|---|---|
| 1 | 你 | 0.00 | speaks "你" |
| 2 | 你好 | 0.67 | skipped |
| 3 | 你好冒 | 0.80 | skipped |
| … | … | … | … |
| 9 | 你好冒险者欢迎来到 | 0.94 | skipped |
| 12 | 你好冒险者欢迎来到新手村 | 0.96 | skipped |

The result: only the first character is ever spoken. The new "read once when the text stops changing" strategy turns
that timeline into: **silent while typing, then the whole line once when it settles.**

## Choosing and tuning

Settings → LION modified → select an area → Edit…; at the bottom of the dialog:

- **Deduplication** — one of the four strategies above.
- **Stable after (ms)** — default 1500, range 500–10000.
  - Fast typing: lower it (e.g. 800) to speak sooner.
  - Slow typing or jittery OCR: raise it (e.g. 2500) so a half-finished line is not spoken as if complete.

**Recommendation for games**: set the dialogue area to "Read once when stable" with 1500 ms.
If a long dialogue box shows many characters and takes a long time to finish, use "Read as it grows" instead.

## One strategy per area

Deduplication is configured **per area**, so a typical setup is:

| Area | Deduplication | Why |
|---|---|---|
| Subtitle strip (video) | Classic | the line appears at once; the original behaviour fits |
| Dialogue box (RPG) | Read once when stable | appears character by character |
| Long text / log area | Read as it grows | content keeps growing; read while it appears |
| A test area | Off | check what OCR actually sees |

Together with **queued speech**: deduplication decides **what** is spoken, queueing decides **how** it is ordered.

## Other behaviour details

- **The box clears, then the same line appears again** (repeated shop dialogue): it is spoken again.
- **Line-by-line append** (line 1 first, then line 2): only the new line 2 is spoken; line 1 is not repeated.
- **The whole line is replaced**: the new text is spoken in full.
- **Text that never stops changing** (animation or a scrolling bar in the area): after at most 10 seconds a single
  forced reading happens, so nothing is silently lost.
- **Upgrading from 3.3.2**: existing areas become "Classic" automatically; no settings need changing.

## Relationship to the original LION

| | Original LION | LION modified |
|---|---|---|
| OCR backend | Bundled PaddleOCR-json.exe subprocess | External PaddleOCR HTTP service (POST /ocr) |
| Add-on size | about 310 MB | about 75 KB |
| Add-on ID | LION | LION-modified (can be installed **alongside** the original) |
| Config section | lion | LION-modified (no overlap) |
| Default gesture | NVDA+Alt+N | NVDA+Alt+N (see "Gesture conflict") |
| OCR areas | one area | **several areas, each with a check box** |
| Deduplication | similarity only | **four strategies, chosen per area** |
| Speech | interrupting | can be switched to **queued** |

The two add-ons do not interfere with each other, but they use **the same default gesture**, so you must resolve that manually — see below.

## Before use: start the external OCR service

This add-on ships **no** OCR engine. A reachable PaddleOCR service is required. For example, deploy it under `<service-deploy-dir>`:

1. Run `启动PaddleOCR-VL.cmd` (or `scripts\start_app.ps1`). The console prints the access token.
2. Open the NVDA menu → Preferences → Settings → **LION modified** and fill in:
   - **OCR service URL**: default `http://127.0.0.1:23353` (base address only, no endpoint)
   - **Model / endpoint**: default `/ocr` (fast profile); use `/ocr-acc` for the accurate profile and
     `/layout-parsing` for PaddleOCR-VL
   - **Service token**: paste the contents of `config\api_token.txt` (leave empty if the service was started with `-NoAuth`)
3. Press **Test / get models**: health → model list → token check → fast-endpoint recognition test,
   with the result shown below the button.

How the address and endpoint combine:

- `http://127.0.0.1:23353` + `/ocr` → `http://127.0.0.1:23353/ocr` (default, fast profile)
- `http://127.0.0.1:23353` + `/ocr-acc` → accurate profile (the service must run with `-OcrAccurate`)
- `http://127.0.0.1:23353` + `/layout-parsing` → PaddleOCR-VL (slow; raise the request timeout)
- The model may also be a full URL; the base address is then ignored, so you can point it at another machine.

For a service on another machine, use `http://that-host:23353/ocr`. The service listens on `0.0.0.0` and is protected by the token only — consider your network exposure.

## How to use it

Press `NVDA+Alt+N` to start or stop. Every second, each **checked** area is recognised once, and text is spoken only when it changes.

For example: check a "subtitle" area and a "chat" area, turn queued speech on, and the new texts from both areas are spoken one after another without interrupting each other.

### Gesture conflict (important)

The original LION and LION modified both default to `NVDA+Alt+N`. With both add-ons enabled this is an **ambiguous binding**: which script runs is not predictable. Pick one of these fixes:

- **Keep only one**: NVDA menu → Preferences → Input gestures → find "Toggle OCR" under the original LION → select `NVDA+Alt+N` in the keys list → press Clear.
- **Give each its own key**: in Input gestures, bind a different shortcut to "Toggle OCR" for either add-on.

## OCR regions (multi-area)

In **Settings → LION modified** there is a region list with three columns: **region name / target / crop**.

- **Check box**: toggle with Space (or the mouse). **Only checked regions are recognised while the feature runs.** Unchecking disables a region without losing its settings.
- **Add**: press "Add…", fill in a name, a target and the four crop percentages, then OK.
- **Edit**: select a row and press Enter (or the "Edit…" button).
- **Remove**: select a row and press the "Remove" button.
- **Order**: use "Move up" / "Move down". **The list order is the order in which queued speech is spoken.**
- **Bulk**: "Enable all" / "Disable all" toggle every check box at once.
- Each region picks its own **target**: navigator object, whole screen, current window, or current control.
- Each region's **crop** is a percentage of that region's target rectangle (top and left offset it; bottom and right shrink it). For example "whole screen + crop 70% from above" is the horizontal strip in the lower 30% of the screen — exactly where subtitles live.
- Top+bottom and left+right may not add up to more than 100%; the editor refuses such combinations.

**Recommendation**: for subtitles, use one or two strip regions and crop about 70% off the top. Fewer and smaller regions make each round faster and produce less irrelevant text.

### Old configurations are migrated automatically

Upgrading from 3.3.1 (or from the original add-on) turns the old "OCR target + four crop values" into a single region named "Area 1" on first start. The recognised area is identical to before — no manual setup needed.

## Queued speech

The settings contain a check box: **Queue speech** (recommended when using several regions).

| | Off (default) | On |
|---|---|---|
| With several regions | Interrupting: each round speaks only the **last region whose text changed** | Every changed region is **queued in list order** and spoken without interrupting each other |
| With one region | Identical to the original | Identical to the original |
| Mechanism | NVDA's "speak now" priority (cuts off the previous utterance) | NVDA's "speak next" priority (waits for the previous utterance) |

Note: with queued speech on, pressing `NVDA+Alt+N` to stop **does not clear texts that are already queued** — they finish playing. Disabling the add-on or quitting NVDA clears the queue.

## Settings

### OCR regions

1. **Region list** — see the section above (the check box decides whether a region takes part).
2. **Deduplication** — one of four strategies per area; see "Choosing and tuning".
3. **Stable after (ms)** — only used by "read once" and "read as it grows"; default 1500.
4. **Queue speech** — see the section above.

### External service

1. **OCR service URL** — see above (base address, no endpoint).
2. **Model / endpoint** — editable combo box. Defaults to `/ocr` and is refreshed from the service by
   "Test / get models". Any custom path or full URL can be typed in.
3. **Service token** — see above. Masked in the dialog; when empty, no authorization header is sent.
4. **Request timeout (ms)** — default 15000. A subtitle strip takes tens of milliseconds, so this is generous; raise it on a slow or busy service.
4. **Image format** — `JPEG` (default, smaller and faster) or `PNG` (lossless, larger). Measured on this machine at 4K full screen: PNG about 364 ms, JPEG about 339 ms.
5. **Return visualisation image** — off by default. When enabled the server draws boxes and encodes an image: full-screen 4K went from about 0.34 s to about 2.0 s. **Not recommended.**
6. **Minimum recognition score (%)** — default 0 (no extra filtering). Raising it discards low-confidence garbage; the service already drops empty lines.
7. **Detection longest side** — default 0, meaning "use the server configuration". Passing 1280 was not faster in our measurements, so nothing is sent by default.
8. **Announce an error once when the OCR service is unavailable** — on by default. Each region announces a failure once and then only logs, so you are not interrupted every second.

### Recognition settings (as in the original)

1. **OCR interval (ms)** — how often OCR runs, from 100 ms to 10 s. **With several regions the times add up**; if a round overruns the interval the next one starts immediately — rounds never overlap.
2. **Text similarity threshold (%)** — default 50; controls when text counts as "changed". **Each region is compared independently.**
3. **Regular expression filters** — one regex per line; matches are removed from the result, e.g. to suppress a video watermark. **Note that the recognised lines are joined into a single line first**, so the anchors `^` and `$` do not match an individual recognised line — to drop a whole line use a plain keyword (such as `Sponsored`) or `[^\s]*keyword[^\s]*`. Leftover whitespace is collapsed automatically.

## Measured performance (this machine, 3840×2160, including base64 encoding and the HTTP round trip)

End-to-end time for a single region:

| Capture area | Format | End-to-end (p50) |
|---|---|---|
| Full screen 3840×2160 | JPEG | **301.6 ms** |
| Full screen 3840×2160 | PNG | 472.1 ms |
| 1920×1080 area | JPEG | **141.9 ms** |
| Subtitle strip 3840×540 | JPEG | **79.3 ms** |
| Subtitle strip 3840×259 | JPEG | **29.7 ms** |
| Full screen + visualisation image | JPEG | about 2000 ms |

**With several regions the total is roughly the sum of the parts** (one request is in flight at a time). For example, a "subtitle strip" plus a "chat" region costs about 100–150 ms — still far below the default one-second interval.

**Recommendation**: for subtitles, always crop to the subtitle strip — it is dramatically faster and produces less irrelevant text.

## Known limitations

- **Text order**: recognised lines are joined in detection order, which is not guaranteed to be reading order. This rarely matters for subtitles; for whole documents use the service's `/layout-parsing` endpoint instead.
- **The external service is mandatory**: with the service down, this add-on cannot recognise anything (each region announces one error and logs afterwards).
- **No document parsing**: this version only talks to `POST /ocr` (screen content OCR). Whole-page → Markdown is a different pipeline and is out of scope.
- **More regions cost more time**: each extra region adds its own capture and OCR round trip. Only check the regions you actually need.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "OCR service unavailable" | Make sure the service window is still open; `http://127.0.0.1:23353/health` should return `Healthy` |
| Unauthorized (HTTP 401) | Wrong token. Copy it again from `config\api_token.txt` (no leading/trailing spaces or newlines) |
| HTTP 400 from the service | The request carried a capability this endpoint does not support; the log contains the server hint and supportedFlags |
| Request timed out | Service busy or image too large; raise the timeout, crop the area, or switch to JPEG |
| Nothing is spoken | Check that at least one region is **checked**; check the similarity threshold; check that your crop settings do not cut the subtitles away |
| Only one region is read | With queued speech off, each round speaks only the last region whose text changed; turn queued speech on to hear every region |
| Gesture does nothing | Almost always the gesture conflict described above |
| It asks you to disable the screen curtain | NVDA limitation: turn off the screen curtain before using OCR |

## Full changelog of this fork

### Version 3.3.6 (LION modified)

- New "Model / endpoint" setting (editable combo box): `/ocr`, `/ocr-acc`, `/layout-parsing`, custom paths or full URLs.
- The service URL is now a base address with the endpoint separate; old configurations migrate automatically and produce
  the same request URL as 3.3.5.
- "Test connection" and "get model list" merged into one button: health → model list → token check → fast recognition test.
- The model list prefers conventional APIs (`/models`, `/v1/models`) and falls back to the server OpenAPI endpoint list,
  labelling the source instead of pretending it is a real model list.
- Basic text extraction for PaddleOCR-VL (`/layout-parsing`): layout blocks in order, Markdown fallback.
- A previous de-duplication regression was fixed; de-duplication and speech behaviour is identical to 3.3.5.

### Version 3.3.7 (LION modified)

- Shorter UI wording, back to the original add-on's concise labels; behaviour identical to 3.3.6.
- New parallel add-on **LION modified lite** (single area, near-original UI, same client as the full edition).
- Version scheme: major 3.3, fork releases use 3.3.N; this release is 3.3.7.

### Version 3.3.3 (LION modified)
1. **Per-area deduplication strategies**: classic (similarity) / read once when the text stops changing / read as it grows / off.
2. "Read once when stable" handles typewriter-style RPG dialogue: the whole line is spoken once it settles.
3. "Read as it grows" handles long dialogue: phrases are released at punctuation as they appear.
4. Existing and migrated areas stay on "Classic", so behaviour is identical to 3.3.2.
5. Dedup logic moved into a standalone `dedup` module and is tested with a virtual clock.
6. Per-area deduplication and queued speech by xiaoshunxing.

### Version 3.3.2 (LION modified)
1. **Multi-area OCR**: the single area became a list of add/edit/remove regions, each with its own check box; every checked region is recognised while the feature runs.
2. **Queued speech**: recognised texts are queued in region order and spoken without interrupting each other; turning it off keeps the interrupting behaviour.
3. Each region chooses its own target (navigator object / whole screen / current window / current control) and its own four crop percentages.
4. Old single-area configurations migrate automatically to one region on first start; behaviour is unchanged.
5. Each region deduplicates and reports failures independently — one failing region does not affect the others.
6. External service integration, multi-area OCR and queued speech by xiaoshunxing.

### Version 3.3.1 (LION modified)
1. The OCR backend changed from the bundled PaddleOCR-json subprocess to an external PaddleOCR HTTP service (`POST /ocr`).
2. New settings: service URL, token, request timeout, image format, visualisation image, minimum score, detection longest side, error notification.
3. New **Test connection** button to verify the URL and token.
4. Fixed: the regex filter could leave stray whitespace and then speak empty content (whitespace is now collapsed).
5. Add-on size dropped from about 310 MB to about 60 KB by removing the bundled engine and the unused bundled PIL.
6. Separate add-on ID and config section, so it can coexist with the original LION.

## Licence and credits

- Original LION: Stefan Moisei, Cary-rowen, anmi2004, hwf1324 — https://github.com/anmi2004/LION
- This fork: **xiaoshunxing** — external PaddleOCR service integration, multi-area OCR, queued speech, settings panel, regex-filter defect fix, packaging slimming
- Licence: GNU GPL v2 or later, same as the original

The feature design and user interface come from the original authors; this fork changes how the recognition backend is reached, how recognition areas are chosen, and how speech is scheduled.
