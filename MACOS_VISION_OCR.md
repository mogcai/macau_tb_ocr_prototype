# macOS Vision OCR — v0.2 integration

In v0.2 Apple Vision is integrated into `macau_tb_ocr.py` as `--engine vision`; this is no longer a standalone smoke test only.

## Design

- Python extracts the original embedded TB image from the PDF via PyMuPDF.
- OpenCV detects the two internal table separators.
- Python creates four region tasks: header, account-label column, debit column, and credit column.
- A bundled Swift worker calls `VNRecognizeTextRequest` in one process per page, with separate Vision requests for each region.
- Account labels use the page script's language: `zh-Hant`, Portuguese (`pt-PT`/`pt-BR` where available), or English. Debit/credit numeric regions use English recognition.
- On bilingual pages both label passes run. Strongly overlapping boxes from different language passes are duplicate readings of the same line, so the script decides: a line containing Chinese keeps the Chinese reading, a Latin line keeps the Portuguese reading. Separate lines are both kept.
- The worker queries locally supported Vision languages and refuses to silently fall back from `zh-Hant` to `zh-Hans`.
- Vision `fast` on current macOS revisions has no Traditional Chinese recognizer (typically only `de-DE`, `en-US`, `es-ES`, `fr-FR`, `it-IT`, `pt-BR`). Any task whose language is Traditional Chinese (`zh-Hant`, `zh`, `zh-HK`, `chinese_cht`, ...) therefore always runs at `accurate`, even when `--vision-level fast` is requested. The requested level and the level actually used are both recorded.
- Each worker observation (worker JSON) also carries `requestedLevel`, `recognitionLevel` and `usesLanguageCorrection`; per-page `vision_levels_used`, `vision_preprocessed` and `vision_language_correction` in `documents.csv` record what actually ran.
- Language correction (`--vision-language-correction`, default `labels`) applies only to header/label regions. Numeric debit/credit regions keep correction off so Vision cannot rewrite an amount before it is stored as raw evidence. `off` and `all` are available for benchmarking.
- Optional grayscale/contrast preprocessing (`--vision-preprocess`, default `off`) runs once per page in the worker. On the issue-45 sample it changed a thousands separator (`147,100.264,290.51` → `147,100,264,290.51`), cost about 15–25% extra time and altered Chinese labels in both directions, so it is opt-in, not a default.
- `--amount-verifier tesseract` and `--row-structure auto|projection` are experimental and off/clustered by default. They add evidence columns and structure hints but produced no measurable accuracy or row-count improvement on issue 45; see the section below before enabling them.
- All OCR text, confidence and pixel coordinates are exported to `ocr_raw.csv`.

Apple's Vision API supports language-prioritised recognition and fast/accurate recognition levels. It also exposes `minimumTextHeight`, a relative text-size threshold; v0.2 sets it to 0.004 by default to retain small text in tall TB images. Vision accepts `usesLanguageCorrection` but has no Chinese lexicon; on the issue-45 sample it changed nothing for `zh-Hant` and only repaired Portuguese accents/typos. It is therefore applied to text regions only, never to the numeric columns.

## Installation

```bash
xcode-select --install
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

No PaddlePaddle/PaddleOCR package is required for `--engine vision`.

## Run

```bash
python macau_tb_ocr.py input.pdf --outdir output_p07 --pages 7 \
  --engine vision --vision-level accurate --ocr-langs auto
```

Speed benchmark:

```bash
python macau_tb_ocr.py input.pdf --outdir output_p07_fast --pages 7 \
  --engine vision --vision-level fast --ocr-langs auto
```

Use different output folders for comparison. Do not treat confidence scores across engines/levels as directly comparable.

`--vision-level fast` applies to the Latin/digit regions only. Because Vision `fast` supports no Traditional Chinese language, Chinese header/label regions are automatically upgraded to `accurate`. Check `documents.csv` `vision_level` (requested) against `vision_levels_used` (actually used). To inspect the raw lists:

```bash
.vision_cache/macau_tb_vision_worker --list-languages fast
.vision_cache/macau_tb_vision_worker --list-languages accurate
```

Language correction and preprocessing (experimental, A/B against `--vision-preprocess off --vision-language-correction off` on the same pages):

```bash
python macau_tb_ocr.py input.pdf --outdir output_p07_prep --pages 7 \
  --engine vision --vision-preprocess on --vision-language-correction all
```

`--vision-language-correction` is `off`, `labels` (default) or `all`; `--vision-preprocess` is `off` (default) or `on`. Do not enable preprocessing for production runs until it has been compared against manual ground truth, because it changes OCR text and number punctuation.

## Amount verifier and row projection (experimental, off by default)

Both features were built and measured on issue 45 (26 TB pages, 1567 rows). They
changed no row on a default run, so they stay opt-in:

- `--amount-verifier tesseract` runs a digit-only Tesseract pass over the debit
  and credit columns and attaches `debit_verifier_raw`, `debit_verifier_status`
  (`agree` / `digit_mismatch` / …) and `debit_verifier_note`. It never replaces
  `debit_raw`/`credit_raw` or a parsed amount. Vision is the stronger reader
  here, so a disagreement only becomes a review reason when Vision's own parse
  already failed. Measured cost: about +25 s per 26-page run; +5 review rows.
- `--row-structure auto|projection` groups boxes by the ink projection of the
  amount columns instead of y-clustering. A guard rejects pages where bands
  merge two reading clusters or disagree with clustering in count/height. The
  guard accepted 4 of 26 pages and produced identical rows, so `clustered`
  remains the default.
- `projection` on a page without the guard can merge rows (issue-45 pages 9,
  11, 12, 16, 20, 24, 26, 27, 30, 32 show why). Always diff row counts before
  trusting it on a new issue.

The verifier and row-projection features have not been validated against manual
ground truth, so no accuracy claim is made for them.

## Amount second pass

`--amount-second-pass ink` (default) re-reads individual amount cells when the
first Vision pass is empty, unparsed, ambiguous or below the confidence
threshold **and the cell contains ink** (Otsu ink fraction ≥ 2%). The cell is cut
at the full amount-column width and the row's vertical extent, upscaled 3×, and
recognised with English/digits only.

- `debit_raw` / `credit_raw` keep the first reading; the re-read goes to
  `debit_second_pass_raw` / `credit_second_pass_raw`.
- Adopted re-reads are stored as `recovered_second_pass` and always reviewed.
- A confident first reading is kept when the re-read disagrees
  (`second_pass_differs`); only a low-confidence, cleanly parsed first reading is
  replaced (`replaced_low_confidence`).
- Per-page counters are written to `documents.csv`
  (`amount_second_pass_cells`, `amount_second_pass_filled`).

On issue 45 this took page 15 from 8 wrong amounts to 0 and did not change any
amount on the other five GT pages. Cost: ~190 cell re-reads, about +30 s for the
26-page run. Use `--amount-second-pass off` to restore the single-pass baseline.

## Region and coordinate rules

ROI coordinates are normalised top-left fractions in the Python input JSON. The Swift worker converts y-coordinates to Vision's normalised lower-left ROI convention. It maps each ROI-relative observation back to full-image pixel coordinates with Apple's `VNImageRectForNormalizedRectUsingRegionOfInterest` conversion, then flips the y-axis to the top-left coordinate system used by Python row reconstruction.

The table body is currently approximated by y=0.035–0.997 and the three columns are derived from the two rightmost detected vertical rules; a leading account-code rule (issue 45 page 29) is used only to trim the label ROI. These are issue-45-oriented defaults and must be revalidated on new Gazette layouts. If lines are not detected, review `column_boundary_source` before accepting data.

## Chinese output policy

- `label_raw` is the exact OCR text returned by Vision.
- `label_normalized` is a separate OpenCC-normalized field (`s2hk` by default).
- The raw field is never overwritten. Script conversion is not treated as proof that the OCR word itself is correct.
- A custom lexicon / custom words is not used for Chinese because Vision does not support Chinese language correction/customWords.

## Limitations and validation status

The worker and adapter are included in the project, but Vision requires macOS and cannot be executed in a Linux build container. The package's Python tests cover number normalization and row validation; the user must compile the Swift worker and run pages 7 and 10 on the actual M1 Mac to validate Vision language availability, ROI coordinate mapping, runtime, and image-level accuracy. Do not claim Vision accuracy or speed improvement until that test is done.
