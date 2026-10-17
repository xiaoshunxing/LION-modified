# LION modified lite

This add-on is the **light edition** of [LION modified (full edition)](readme.md): its settings keep the
single-area, near-original layout of LION, while the recognition backend uses an external PaddleOCR HTTP
service and the client is identical to the full edition **3.3.7**.

## What it does

Press `NVDA+Alt+N` to start or stop. While running it OCRs the configured area every interval and only speaks
text that has changed noticeably. It is meant for subtitles, game dialogue and any text NVDA cannot read directly.

## Differences from the full edition

| | lite (this add-on) | full LION modified |
|---|---|---|
| OCR area | **one area** (target + four-way crop) | several areas, checkable and sortable |
| De-duplication | classic similarity (as in the original) | classic / read once when stable / read as it grows / off, per area |
| Queued speech | no | yes |
| Settings | few, close to the original | many (multi-area, strategies, ...) |
| Client | same as the full edition | same |

Both add-ons can be installed **at the same time** (IDs `LION-modified-lite` and `LION-modified`) and their
settings are independent. They share the default gesture `NVDA+Alt+N`, so rebind one of them in Input Gestures.

## Before use: start the external OCR service

The add-on ships no OCR engine; a PaddleOCR service is required (default `http://127.0.0.1:23353`).

NVDA menu → Preferences → Settings → **LION modified lite**:

- **OCR service URL**: base address only, e.g. `http://127.0.0.1:23353`
- **Model / endpoint**: `/ocr` by default; `/ocr-acc` for the accurate profile; `/layout-parsing` for PaddleOCR-VL
- **Service token**: the token printed by the service (leave empty when started with `-NoAuth`)

The **Test / get models** button performs a health check, reads the model list, verifies the token and runs a
fast-endpoint recognition test in one go.

## Settings

### Service

1. **OCR service URL** – base address without endpoint; the model may also be a full URL pointing at another machine.
2. **Model / endpoint** – editable combo box, `/ocr` by default.
3. **Service token** – masked; empty means no authorization header.
4. **Request timeout (ms)** – 15000 by default; raise it for PaddleOCR-VL (max 120000).
5. **Image format**, **return visualisation image (slow)**, **minimum score**, **detection longest side**,
   **announce errors once** – as in the full edition; detection longest side and minimum score have no effect on
   `/layout-parsing` (the server ignores them).

### Recognition

1. **OCR interval (ms)** – 1000 by default.
2. **OCR target** – navigator object / whole screen / current window / current control.
3. **Text similarity threshold (%)** – 50 by default; text is spoken only when similarity with the previous result is below it.
4. **Crop above/below/left/right (%)** – crop percentages; about 70% from above is typical for subtitles.
5. **Regular expression filters** – one regex per line; matches are removed from the result.

## Versioning

The major version follows upstream **3.3** (original LION 3.3.0); fork releases use **3.3.N** and this release is
**3.3.7**. Release packages are distributed through GitHub Releases.

## Licence and credits

- Based on [LION](https://github.com/anmi2004/LION) by Stefan Moisei, Cary-rowen, anmi2004 and hwf1324.
- Distributed under the **GNU General Public License v2 or later** (GPL-2.0-or-later); the full text is in
  `COPYING.txt` in the bundle or repository.
- Original authorship is fully preserved; light edition and UI wording by xiaoshunxing.
- This is a third-party modification, not affiliated with NV Access or the original authors.
