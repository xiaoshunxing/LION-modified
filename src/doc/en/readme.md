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

# Changes in this fork (LION modified 3.3.8)

The backend uses an external PaddleOCR HTTP service instead of LION's bundled engine. The add-on supports several OCR
areas, per-area de-duplication, queued speech and a service test — the controls useful for screen-reader users.

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

Both add-ons can be installed together because their IDs, package names and configuration sections differ; the shared default gesture needs rebinding.

## Running the OCR service

LION modified ships no OCR engine: it captures the screen with NVDA's libraries, encodes the image and sends it to a
PaddleOCR HTTP service, which performs all recognition. A local service is recommended: latency is lower and the token
stays local. The examples below use `http://127.0.0.1:23353`; replace these values with those of the deployment.

The service decides which endpoints exist; these are common examples, not fixed parts of the add-on:

- `/ocr` — fast screen OCR, the usual choice for subtitles.
- `/ocr-acc` — an accurate profile, when the deployment enables it.
- `/layout-parsing` — PaddleOCR-VL document parsing; slower, so raise the request timeout.

Configure the connection under **NVDA menu → Preferences → Settings → LION modified**:

1. **OCR service URL** — base address only, for example `http://127.0.0.1:23353`; do not append an endpoint.
2. **Model / endpoint** — `/ocr` initially; choose a listed endpoint or type another path or full URL.
3. **Service token** — the token issued by the deployment; masked, and empty sends no authorization header.
4. Press **Test / get models** and read the result below the button.

The request URL is the base address plus the endpoint: `http://127.0.0.1:23353` + `/ocr` →
`http://127.0.0.1:23353/ocr`. The same rule applies to `/ocr-acc` and `/layout-parsing`. A full URL in **Model /
endpoint** replaces the base address, so the service can run on another host.

### Test / get models

One button runs four checks and reports each result below it:

1. **Health** on `/health`.
2. **Model list** — `GET /models`, then `GET /v1/models`, then the OpenAPI endpoint list; the source is labelled when the result is not a true model list. The combo box is editable, so an unlisted endpoint can be typed in.
3. **Token** — a request body without `file` returns 401 for a bad token and 400 or 422 for a good one; no inference runs.
4. **Recognition** — an 8×8 white image goes to fast endpoints; skipped for `/layout-parsing`.

### PaddleOCR-VL notes

`/layout-parsing` is a document parser and much slower; raise the timeout (initial 15 s, maximum 120 s). Only text is used,
in layout-block order; tables, formulas, charts and seals are skipped. Detection longest side and minimum recognition score have no effect.

## How to use it

Press `NVDA+Alt+N` to start or stop. Each cycle captures and recognises every checked area once and speaks only text
that counts as changed. With a subtitle area and a chat area checked and queued speech on, both are spoken without
interruption.

### OCR areas

**Settings → LION modified** contains a region list with three columns: region name, target and crop.

- **Check box** — Space or the mouse toggles an area; only checked areas run, and unchecking keeps the settings.
- **Add / Edit / Remove** — use the buttons or Enter; each area has a name, a target and four crop percentages.
- **Order and bulk** — "Move up" / "Move down" set queued speech order; "Enable all" / "Disable all" toggle every area.
- **Target** — navigator object, whole screen, current window or current control.
- **Crop** — percentages of the target rectangle: top and left offset it, bottom and right shrink it. "Whole screen + crop 70% from above" gives the lower 30% strip where subtitles usually sit.
- Top plus bottom, and left plus right, may not exceed 100%; the editor refuses such combinations.

For subtitles, one or two strip areas with about 70% cropped from the top work well: smaller areas are faster and
produce less irrelevant text. Upgrading from an earlier single-area edition, or from the original add-on, converts the
old target and crop values into one area named "Area 1" on first start; the recognised region is unchanged.

## Gesture conflict

The original LION and LION modified both default to `NVDA+Alt+N`; with both enabled the binding is ambiguous. Clear the
key on one add-on, or bind a different shortcut to "Toggle OCR" for either in NVDA's Input Gestures dialog.

## De-duplication

Classic de-duplication assumes text appears complete in one frame, as subtitles do. Typewriter dialogue arrives one
character at a time, so each frame extends the previous one and similarity keeps climbing; after the first character is
spoken, the rest may be judged unchanged. This fork adds strategies for that case, chosen per area.

| Strategy | Best for | Behaviour |
|---|---|---|
| Classic (similarity) **default** | Subtitles, text that appears at once | Speaks when similarity to the previous text is below the threshold; the original behaviour |
| Read once when stable | **RPG dialogue, typewriter effects** | Waits until text stops growing, then speaks the whole line once |
| Read as it grows | Very long dialogue (10 s or more) | Releases phrases at punctuation while text is still being typed |
| Off (every change) | Troubleshooting | Speaks every change |

A strategy is chosen under Settings → LION modified → select an area → Edit…; **Stable after (ms)** defaults to 1500
(range 500–10000). Lower it for fast typing, raise it for slow typing or jittery OCR. For games, **Read once when
stable** at 1500 ms is recommended; for very long dialogue use **Read as it grows**. De-duplication decides **what** is
spoken; queued speech decides **in which order**.

**Per-area classic threshold**: only Classic uses the similarity threshold, and each area has its own value by
default ("Use global similarity threshold" checked, i.e. the panel's default). Uncheck it to set a value from 0 to
100 for that area only. The text is spoken when similarity is **below** the threshold, so a lower value is
stricter and a higher value speaks more often; a digit/template change (for example "Round 1" → "Round 2") is very
similar, so raise that area's threshold if you want to hear it. The other strategies ignore the threshold.

### Behaviour details

- **The box clears, then the same line appears again** (repeated shop dialogue): the line is spoken again by design.
- **Line-by-line append** (line 1, then line 2): only the new line 2 is spoken; line 1 is not repeated.
- **The whole line is replaced**: the new text is spoken in full.
- **Text that never stops changing**: one forced reading happens after at most 10 seconds, so nothing is lost.
- **Two areas cover the same text**: each area de-duplicates on its own, so the text is read twice; keep one.
- **Punctuation-only fragments** (a stray `!`) are not spoken alone, while digit changes (`frame 1` → `frame 2`) count as new content and are never swallowed as jitter.

## Queued speech

**Queue speech** is recommended when several areas are active.

| | Off (default) | On |
|---|---|---|
| Several areas | Each cycle speaks only the last area whose text changed | Every changed area is queued in list order and spoken without interruption |
| Mechanism | NVDA's "speak now" priority, cutting off the previous utterance | NVDA's "speak next" priority, waiting for the previous utterance |

With queued speech on, pressing `NVDA+Alt+N` to stop does not clear texts that are already queued; they finish playing.
Disabling the add-on or quitting NVDA clears the queue.

## Merge region capture (optional)

The settings include a check box: **Merge region capture** (off by default).

| | Off (default) | On |
|---|---|---|
| Captures | Each checked area is captured separately (N areas = N captures) | The union of all areas is captured once and then cropped per area (1 capture) |
| Best for | Areas far apart on a large screen | Areas close together or overlapping (for example a subtitle strip directly above a dialogue box) |
| Cost | The number of captures grows with the number of areas | The union can be much larger than a single area; the farther apart the areas, the slower the single capture |

Recommendation: leave it off; turn it on when you have two or three areas that sit **close together** and capture time seems to be the bottleneck, then compare the actual latency before deciding.
Even with it on, a failed union capture or crop falls back to per-area captures automatically, so no information is lost.

## Configuration profiles

- **NVDA 2026.3 or later**: this add-on's settings are **always stored in the base configuration** and do not follow
  configuration profiles or triggers. Switching a profile does not change speech behaviour, and saving settings
  writes only to the base `nvda.ini`.
- After the first install/upgrade, **restart NVDA** to switch to this global mode. Values this add-on previously
  saved into a profile are ignored; save the settings again if needed.
- **NVDA 2026.1/2026.2**: the API does not exist, so the add-on still follows the active profile; edit settings
  under the base configuration (with no profile active).

## Settings

### External service

1. **Request timeout (ms)** — default 15000; raise it for a slow or busy service.
2. **Image format** — `JPEG` (default, smaller and faster) or `PNG` (lossless, larger and slower).
3. **Return visualisation image (slow)** — off by default; drawing and encoding make it substantially slower.
4. **Minimum recognition score (%)** — default 0; raising it discards low-confidence text.
5. **Detection longest side (0 = default)** — 0 uses the server configuration; sending 1280 has not proved faster.
6. **Announce errors once** — on by default; a failure is announced once and then only logged.
7. **Merge region capture** — off by default. When on, all checked areas are captured in one screenshot and cropped locally, which reduces the number of captures; areas far apart can make it slower, see the section above.

### Recognition settings

1. **OCR interval (ms)** — 100 ms to 10 s. With several areas the times add up; a cycle that overruns starts the next immediately, and cycles never overlap.
2. **Default text similarity threshold (%)** — default 50; the default for Classic. Each area can override it in
   the editor (see "De-duplication"). Text is spoken when similarity is **below** the threshold, so a lower value
   is stricter; it only affects Classic, not "Read once when stable", "Read as it grows" or "Off".
3. **Regular expression filters (one per line)** — matches are removed from the result, for example to suppress a watermark. Lines are joined into one before filtering, so `^` and `$` do not match an individual line; use a plain keyword such as `Sponsored` or `[^\s]*keyword[^\s]*`; leftover whitespace is collapsed.

## Performance reference

Reference environment: 3840×2160; the values are indicative and depend on the hardware and service.

| Capture area | Format | End-to-end (p50) |
|---|---|---|
| Full screen | JPEG | **302 ms** |
| Full screen | PNG | 472 ms |
| 1920×1080 area | JPEG | **142 ms** |
| Subtitle strip 3840×540 | JPEG | **79 ms** |
| Subtitle strip 3840×259 | JPEG | **30 ms** |

Area size dominates the cost; with several areas the total is roughly the sum of the parts, and cropping to the subtitle strip remains the largest saving.

## Known limitations

- **Text order** — lines are joined in detection order, not guaranteed reading order. Use `/layout-parsing` for whole documents.
- **The external service is required** — with the service down, nothing can be recognised; each area announces one error and then logs.
- **No document parsing on `/ocr`** — whole-page to Markdown is a different pipeline; use `/layout-parsing`.
- **More areas cost more time** — each adds its own capture and OCR request; check only the areas needed.

## Troubleshooting

| Symptom | What to do |
|---|---|
| "OCR service unavailable" | Confirm that the service is running and that `http://127.0.0.1:23353/health` returns `Healthy` |
| Unauthorized (HTTP 401) | The token is wrong or malformed; copy it again with no leading or trailing whitespace |
| HTTP 400 from the service | The endpoint does not support a capability in the request; the log has the server hint and supportedFlags |
| Request timed out | The service is busy or the image is too large; raise the timeout, crop the area, or use JPEG |
| Nothing is spoken | Verify that at least one area is **checked**; check that the "Default text similarity threshold" is not **too low** (lower is stricter; 0 speaks almost nothing); confirm that the crop keeps the subtitles |
| One area is never spoken | Check the area's de-duplication strategy: under Classic, a change too similar to the last spoken text is skipped (digit/template changes are common — raise that area's threshold); "Read once when stable"/"Read as it grows" wait for stable text or punctuation; with queued speech off only the last changed area is spoken per cycle. For details, set the NVDA log level to debug; the add-on logs the effective threshold and the decision per area |
| Settings do not follow configuration profiles | Expected from 3.3.8 on NVDA 2026.3+: the add-on stores settings in the base configuration; older versions still follow profiles |
| Only one area is read | With queued speech off, each cycle speaks only the last changed area; turn queued speech on |
| Gesture does nothing | Almost always the gesture conflict described above |
| The add-on asks for the screen curtain to be disabled | NVDA limitation: turn off the screen curtain before using OCR |

## Version

Current version 3.3.8; full changes are in [CHANGELOG.md](https://github.com/xiaoshunxing/LION-modified/blob/main/CHANGELOG.md).

## Licence and credits

- Original LION: Stefan Moisei, Cary-rowen, anmi2004, hwf1324 — https://github.com/anmi2004/LION
- This add-on: **xiaoshunxing** — external PaddleOCR service integration, multi-area OCR, per-area de-duplication, queued speech, settings panel, regex-filter fix and packaging cleanup.
- Licence: GNU GPL v2 or later, the same as the original.
- Feature design and interface come from the original authors; the changes in this fork are the recognition backend, the area model, de-duplication and speech scheduling.

[![powered by dsh](../dsh-badge.png)](https://github.com/deepseek-ai/deepseek-harness)
