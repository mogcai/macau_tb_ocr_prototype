# macOS Vision OCR — v0.2 integration

In v0.2 Apple Vision is integrated into `macau_tb_ocr.py` as `--engine vision`; this is no longer a standalone smoke test only.

## Design

- Python extracts the original embedded TB image from the PDF via PyMuPDF.
- OpenCV detects the two internal table separators.
- Python creates four region tasks: header, account-label column, debit column, and credit column.
- A bundled Swift worker calls `VNRecognizeTextRequest` in one process per page, with separate Vision requests for each region.
- Account labels use the page script's language: `zh-Hant`, Portuguese (`pt-PT`/`pt-BR` where available), or English. Debit/credit numeric regions use English recognition.
- The worker queries locally supported Vision languages and refuses to silently fall back from `zh-Hant` to `zh-Hans`.
- All OCR text, confidence and pixel coordinates are exported to `ocr_raw.csv`.

Apple's Vision API supports language-prioritised recognition and fast/accurate recognition levels. It also exposes `minimumTextHeight`, a relative text-size threshold; v0.2 sets it to 0.004 by default to retain small text in tall TB images. The Chinese recognition route does not support language correction/customWords, so the worker disables correction and preserves raw output.

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

## Region and coordinate rules

ROI coordinates are normalised top-left fractions in the Python input JSON. The Swift worker converts y-coordinates to Vision's normalised lower-left ROI convention. It maps each ROI-relative observation back to full-image pixel coordinates with Apple's `VNImageRectForNormalizedRectUsingRegionOfInterest` conversion, then flips the y-axis to the top-left coordinate system used by Python row reconstruction.

The table body is currently approximated by y=0.035–0.985 and the three columns are derived from the two detected vertical rules. These are issue-45-oriented defaults and must be revalidated on new Gazette layouts. If lines are not detected, review `column_boundary_source` before accepting data.

## Chinese output policy

- `label_raw` is the exact OCR text returned by Vision.
- `label_normalized` is a separate OpenCC-normalized field (`s2hk` by default).
- The raw field is never overwritten. Script conversion is not treated as proof that the OCR word itself is correct.
- A custom lexicon / custom words is not used for Chinese because Vision does not support Chinese language correction/customWords.

## Limitations and validation status

The worker and adapter are included in the project, but Vision requires macOS and cannot be executed in a Linux build container. The package's Python tests cover number normalization and row validation; the user must compile the Swift worker and run pages 7 and 10 on the actual M1 Mac to validate Vision language availability, ROI coordinate mapping, runtime, and image-level accuracy. Do not claim Vision accuracy or speed improvement until that test is done.
