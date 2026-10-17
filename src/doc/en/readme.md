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

We rebuilt LION's recognition backend on an external PaddleOCR HTTP service and added the controls we
wanted as screen-reader users: several OCR areas, per-area de-duplication, queued speech and a service test.

## Relationship to the original LION

| | Original LION | LION modified |
|---|---|---|
| OCR backend | Bundled PaddleOCR-json.exe subprocess | External PaddleOCR HTTP service (endpoint selectable) |
| Add-on size | about 310 MB | about 100 KB |
| Add-on ID | LION | LION-modified (can be installed **alongside** the original) |
| Config section | lion | LION-modified (no overlap) |
| Default gesture | NVDA+Alt+N | NVDA+Alt+N (see "Gesture conflict") |
| OCR areas | one area | **several areas, each with a check box** |
| De-duplication | similarity only | **four strategies, chosen per area** |
| Speech | interrupting | can be switched to **queued** |

Both add-ons can be installed together because their IDs, package names and configuration sections differ.
They share the default gesture, so rebind one; see Gesture conflict.

## Running the OCR service

This add-on ships no OCR engine; it sends screenshots to a PaddleOCR HTTP service, while screen capture and image encoding use NVDA's own libraries (no Pillow).

We recommend running the PaddleOCR HTTP service locally: lower latency and the token stays on your machine. The examples below use `http://127.0.0.1:23353`; replace the address, endpoint and token with your own deployment.

The service decides which endpoints exist. These are common examples, not fixed parts of this add-on:

- `/ocr` — fast screen OCR, our usual choice for subtitles.
- `/ocr-acc` — an accurate profile, when the deployment enables it.
- `/layout-parsing` — PaddleOCR-VL document parsing; slower, so raise the request timeout.

Configure it under **NVDA menu → Preferences → Settings → LION modified**:

1. **OCR service URL** — base address only, for example `http://127.0.0.1:23353`; do not append an endpoint.
2. **Model / endpoint** — `/ocr` initially; choose a listed endpoint or type another path or full URL.
3. **Service token** — the token issued by your deployment; masked, and empty sends no authorization header.
4. Press **Test / get models** and read the result below the button.

The request URL is the base address plus the endpoint: `http://127.0.0.1:23353` + `/ocr` →
`http://127.0.0.1:23353/ocr`; `/ocr-acc` and `/layout-parsing` work the same way. A full URL in
**Model / endpoint** replaces the base address, so the service may run on another host.

### Test / get models
One button runs four checks and reports each result below it:

1. **Health** on `/health`.
2. **Model list** — `GET /models`, then `GET /v1/models`, then the OpenAPI endpoint list; the source is labelled when the result is not a true model list.
3. **Token** — a request body without `file` returns 401 for a bad token and 400 or 422 for a good one; no inference runs.
4. **Recognition** — an 8×8 white image goes to fast endpoints; skipped for `/layout-parsing`.

The combo box is editable, so an unlisted endpoint can be typed in.

### PaddleOCR-VL notes
`/layout-parsing` is a document parser and much slower; raise the timeout (initial 15 s, maximum 120 s). Only text is used, in layout-block order; tables, formulas, charts and seals are skipped. Settings such as detection longest side and minimum recognition score have no effect; leave them at their defaults.

## How to use it

Press `NVDA+Alt+N` to start or stop. During each cycle every checked area is captured and recognised once;
text is spoken only when it counts as changed. Check a subtitle area and a chat area, turn on queued speech,
and new text from both areas is spoken without interruption.

### Gesture conflict
The original LION and this add-on both default to `NVDA+Alt+N`; with both enabled the binding is ambiguous.
Clear the key on one add-on, or bind a different shortcut to "Toggle OCR" for either under NVDA menu → Preferences → Input gestures.

## OCR areas

**Settings → LION modified** contains a region list with three columns: region name, target and crop.

- **Check box** — Space or the mouse toggles an area; only checked areas run, and unchecking keeps settings.
- **Add / Edit / Remove** — use the buttons or Enter; each area has a name, target and four crop percentages.
- **Order and bulk** — "Move up" / "Move down" set queued speech order; "Enable all" / "Disable all" toggle all.
- **Target** — navigator object, whole screen, current window or current control.
- **Crop** — percentages of the target rectangle: top and left offset it, bottom and right shrink it. "Whole
  screen + crop 70% from above" is the lower 30% strip where subtitles usually sit.
- Top plus bottom, and left plus right, may not exceed 100%; the editor refuses such combinations.

For subtitles, use one or two strip areas with about 70% cropped from the top; smaller areas are faster and
produce less irrelevant text.

### Existing configurations
Upgrading from an earlier single-area edition, or from the original add-on, converts the old target and crop
values into one area named "Area 1" on first start; the recognised region is unchanged.

## De-duplication

Classic de-duplication assumes text appears complete in one frame, as subtitles do. Typewriter dialogue arrives
one character at a time, so each frame extends the previous one and similarity keeps climbing; after the first
character is spoken, the rest may be judged unchanged. We added strategies for that case, chosen per area.

| Strategy | Best for | Behaviour |
|---|---|---|
| Classic (similarity) **default** | Subtitles, text that appears at once | Speaks when similarity to the previous text is below the threshold; the original behaviour |
| Read once when stable | **RPG dialogue, typewriter effects** | Waits until text stops growing, then speaks the whole line once |
| Read as it grows | Very long dialogue (10 s or more) | Releases phrases at punctuation while text is still being typed |
| Off (every change) | Troubleshooting | Speaks every change |

Choose a strategy under Settings → LION modified → select an area → Edit…; **Stable after (ms)** defaults to
1500 (range 500–10000): lower it for fast typing, raise it for slow typing or jittery OCR. For games, we
recommend **Read once when stable** at 1500 ms; for very long dialogue, use **Read as it grows** instead.
De-duplication decides **what** is spoken; queued speech decides **in which order**.

### Behaviour details

- **The box clears, then the same line appears again** (repeated shop dialogue): the line is spoken again by
  design.
- **Line-by-line append** (line 1, then line 2): only the new line 2 is spoken; line 1 is not repeated.
- **The whole line is replaced**: the new text is spoken in full.
- **Text that never stops changing**: one forced reading happens after at most 10 seconds, so nothing is lost.
- **Two areas cover the same text**: each area de-duplicates on its own, so the text is read twice; keep one.
- **Punctuation-only fragments** (a stray `!`) are not spoken alone, while digit changes (`frame 1` →
  `frame 2`) count as new content and are never swallowed as jitter.

## Queued speech

The settings include **Queue speech**, which we recommend when several areas are active.

| | Off (default) | On |
|---|---|---|
| Several areas | Each cycle speaks only the last area whose text changed | Every changed area is queued in list order and spoken without interruption |
| Mechanism | NVDA's "speak now" priority, cutting off the previous utterance | NVDA's "speak next" priority, waiting for the previous utterance |

With queued speech on, pressing `NVDA+Alt+N` to stop does not clear texts that are already queued; they finish playing. Disabling the add-on or quitting NVDA clears the queue.

## Settings

### OCR areas

1. **Region list** — see OCR areas above; the check box decides whether an area takes part.
2. **De-duplication** — one of the four strategies per area; see De-duplication.
3. **Stable after (ms)** — used by "Read once when stable" and "Read as it grows"; default 1500.
4. **Queue speech** — see Queued speech.

### External service

1. **OCR service URL** — the base address, with no endpoint.
2. **Model / endpoint** — editable combo box, starting at `/ocr` and refreshed by **Test / get models**.
3. **Service token** — masked; empty means no authorization header.
4. **Request timeout (ms)** — default 15000; raise it for a slow or busy service.
5. **Image format** — `JPEG` (default, smaller and faster) or `PNG` (lossless, larger and slower).
6. **Return visualisation image (slow)** — off by default; drawing and encoding make it substantially slower.
7. **Minimum recognition score (%)** — default 0; raising it discards low-confidence text.
8. **Detection longest side (0 = default)** — 0 uses the server configuration; sending 1280 has not proved faster.
9. **Announce errors once** — on by default; a failure is announced once and then only logged.

### Recognition settings

1. **OCR interval (ms)** — 100 ms to 10 s. With several areas the times add up; a cycle that overruns starts
   the next immediately, and cycles never overlap.
2. **Text similarity threshold (%)** — default 50; controls when text counts as changed. Each area is compared
   independently.
3. **Regular expression filters (one per line)** — matches are removed from the result, for example to suppress a watermark. Lines are joined into one before filtering, so `^` and `$` do not match an individual line; use a plain keyword such as `Sponsored` or `[^\s]*keyword[^\s]*`; leftover whitespace is collapsed.

## Performance reference

Test environment: 3840×2160; reference values, actual results depend on the machine and service.

| Capture area | Format | End-to-end (p50) |
|---|---|---|
| Full screen | JPEG | **302 ms** |
| Full screen | PNG | 472 ms |
| 1920×1080 area | JPEG | **142 ms** |
| Subtitle strip 3840×540 | JPEG | **79 ms** |
| Subtitle strip 3840×259 | JPEG | **30 ms** |

Area size dominates the cost; with several areas the total is roughly the sum of the parts, and cropping to the subtitle strip remains the largest saving.

## Known limitations

- **Text order** — lines are joined in detection order, not guaranteed reading order. Use `/layout-parsing` for
  whole documents.
- **The external service is required** — with it down, this add-on cannot recognise anything; each area
  announces one error and then logs.
- **No document parsing on `/ocr`** — whole-page to Markdown is a different pipeline; use `/layout-parsing`.
- **More areas cost more time** — each adds its own capture and OCR request; check only the areas you need.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "OCR service unavailable" | Make sure the service is running and `http://127.0.0.1:23353/health` returns `Healthy` |
| Unauthorized (HTTP 401) | The token is wrong or malformed; copy it again with no leading or trailing whitespace |
| HTTP 400 from the service | The endpoint does not support a capability in the request; the log has the server hint and supportedFlags |
| Request timed out | The service is busy or the image is too large; raise the timeout, crop the area, or use JPEG |
| Nothing is spoken | Check that at least one area is **checked**, review the threshold, and make sure the crop keeps the subtitles |
| Only one area is read | With queued speech off, each cycle speaks only the last changed area; turn queued speech on |
| Gesture does nothing | Almost always the gesture conflict described above |
| It asks you to disable the screen curtain | NVDA limitation: turn off the screen curtain before using OCR |

## Full changelog

### Version 3.3.7 (LION modified)
- Shorter labels for de-duplication, area editing and service settings, back to the original add-on's concise
  wording; status messages are shorter too, and behaviour is unchanged.
- Added the parallel **lite** add-on: one area, the near-original interface and the same client as the full
  edition. Different IDs, package names and configuration sections let both coexist; rebind one gesture.

### Version 3.3.6 (LION modified)
- New **Model / endpoint** setting: editable combo box with `/ocr`, `/ocr-acc`, `/layout-parsing`, custom paths
  and full URLs; the service URL became a base address, and old configurations migrate automatically.
- Merged **Test connection** and **get model list** into **Test / get models**; model discovery prefers
  `/models` and `/v1/models`, then falls back to the OpenAPI list. Added basic PaddleOCR-VL text extraction and
  fixed a de-duplication regression.

### Version 3.3.5 (LION modified)
- Fixed repeated reading in "Read as it grows" after a sentence had settled.
- Jitter detection now uses a length-aware edit distance instead of a common-prefix ratio; a fuzzy anchor finds
  the resume point after a misread character, punctuation-only fragments are no longer spoken alone, and digit
  changes count as new content.

### Version 3.3.4 (LION modified)
- "Read once when stable" no longer re-announces a sentence after OCR jitter; frames are classified as same,
  jitter, growth or replacement, and jitter never clears the announced record.
- "Read as it grows" resumes from the content actually spoken instead of the whole previous frame; the
  10-second guard fires only when content actually stops growing, and an empty frame no longer resets it.

### Version 3.3.3 (LION modified)
- **Per-area de-duplication**: Classic (similarity), Read once when stable, Read as it grows and Off (every
  change); existing and migrated areas stay on Classic.
- The logic moved into a standalone `dedup` module tested with a virtual clock.

### Version 3.3.2 (LION modified)
- **Multi-area OCR** and **queued speech**: checked areas run each cycle, and changed texts are queued in area
  order without interrupting each other.
- Each area chooses its own target and four crop percentages; old single-area configurations migrate
  automatically, and each area de-duplicates and reports failures independently.

### Version 3.3.1 (LION modified)
- The backend changed from the bundled PaddleOCR-json subprocess to an external PaddleOCR HTTP service, with
  settings for URL, token, timeout, image format, visualisation, minimum score, detection longest side and
  errors.
- Added a **Test connection** button and fixed a regex-filter defect that could leave whitespace and speak
  empty content. The package shrank from about 310 MB to about 60 KB; separate IDs and configuration
  sections allow coexistence with the original LION.

## Licence and credits

- Original LION: Stefan Moisei, Cary-rowen, anmi2004, hwf1324 — https://github.com/anmi2004/LION
- This add-on: **xiaoshunxing** — external PaddleOCR service integration, multi-area OCR, per-area
  de-duplication, queued speech, settings panel, regex-filter fix and packaging cleanup.
- Licence: GNU GPL v2 or later, the same as the original.
- Feature design and interface come from the original authors; our changes are the recognition backend, the
  area model, de-duplication and speech scheduling.

AI disclosure: New text in this modification was created by us with AI assistance; upstream original text is excluded. Model: DeepSeek-V4.1-Flash.
