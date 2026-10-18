# LION modified lite

This is the lite edition of [LION modified (full edition)](https://github.com/xiaoshunxing/LION-modified/blob/main/src/doc/en/readme.md):
it keeps the single-area, near-original layout of LION, uses an external PaddleOCR HTTP service, and shares
the same client as the full edition **3.3.7**.

## What it does

Press `NVDA+Alt+N` to start or stop. While running it OCRs the configured area every interval and only speaks
text that has changed noticeably. It is meant for subtitles, game dialogue and any text NVDA cannot read directly.

## Differences from the full edition

| | Full edition `LION-modified` | lite `LION-modified-lite` |
|---|---|---|
| OCR area | Several areas, checkable and sortable | One area, close to the original |
| De-duplication | Classic / read once when stable / read as it grows / off | Classic similarity |
| Queued speech | Yes | No |
| Settings | Many | Few, close to the original |
| Client | Same: models/endpoints, model list, PaddleOCR-VL text extraction | Same |

Both add-ons can be installed **at the same time** (different IDs, package names and config sections) and their
settings are independent. They share the default gesture `NVDA+Alt+N`, so rebind one of them.

## Before use: prepare the external OCR service

We recommend running the PaddleOCR HTTP service locally: lower latency and the token stays on your machine.
The examples below use `http://127.0.0.1:23353`; replace the address, endpoint and token with your own deployment.

Open NVDA menu → Preferences → Settings → **LION modified lite** and fill in:

- **OCR service URL**: example `http://127.0.0.1:23353`, base address only.
- **Model / endpoint**: common examples are `/ocr`, `/ocr-acc` and `/layout-parsing`; use what your service provides.
- **Service token**: obtain it from your service.

The **Test / get models** button performs a health check, reads the model list, verifies the token and runs a
fast-endpoint recognition test in one go.

## Settings

### Service

1. **Request timeout (ms)** – initial value 15000; raise it for `/layout-parsing` (max 120000).
2. **Image format**, **return visualisation image (slow)**, **minimum score**, **detection longest side**, **announce errors once** – as in the full edition; detection longest side and minimum score have no effect on `/layout-parsing` (the server ignores them).

### Recognition

1. **OCR interval (ms)** – initial value 1000.
2. **OCR target** – navigator object / whole screen / current window / current control.
3. **Text similarity threshold (%)** – initial value 50; text is spoken only when similarity with the previous result is below it.
4. **Crop above/below/left/right (%)** – crop percentages; about 70% from above is typical for subtitles.
5. **Regular expression filters** – one regex per line; matches are removed from the result.

## Versioning

The major version follows upstream **3.3** (original LION 3.3.0); releases use **3.3.N** and this release is
**3.3.7**. Packages are distributed through GitHub Releases.

## Licence and credits

- Based on [LION](https://github.com/anmi2004/LION) by Stefan Moisei, Cary-rowen, anmi2004 and hwf1324.
- Distributed under the **GNU General Public License v2 or later** (GPL-2.0-or-later); the full text is in `COPYING.txt` in the bundle or repository.
- Original authorship is fully preserved; lite edition and UI wording by xiaoshunxing.
- This add-on is based on LION; we are not affiliated with NV Access or the original authors.

AI disclosure: New text in this modification was created with AI participation; upstream original text is excluded.

Model version: DeepSeek-V4.1-Flash.
