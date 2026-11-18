# macOS Setup — Macau Gazette Balancete OCR v0.7.0

## Recommended route for M1 / M2 / M3 / M4

v0.3 uses **Apple Vision as the default OCR engine on macOS**. It is a native macOS framework called by a small Swift worker. You do not need to install PaddlePaddle or PaddleOCR to use `--engine vision`.

### 1. Check architecture and install Swift compiler

```bash
uname -m
python3 -c "import platform,sys; print('machine=', platform.machine()); print('python=', sys.executable); print('version=', platform.python_version())"
swift --version
```

For an M1 MacBook Air, both `uname -m` and `platform.machine()` should normally report `arm64` when using native Terminal and Python.

If `swiftc` is missing:

```bash
xcode-select --install
```

The Vision framework is part of macOS. No Python binding / Paddle runtime is needed for Vision.

### 2. Create a clean Python environment

Use Python 3.10 or 3.11 for a conservative setup:

```bash
cd /path/to/macau_tb_ocr_prototype_v0.4.1
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
```

`requirements.txt` contains PDF/image processing, Excel export, and OpenCC. It deliberately does **not** install PaddleOCR or PaddlePaddle.

### 3. Check Vision languages available on this Mac

```bash
swiftc -O vision_ocr_worker.swift -o /tmp/macau_tb_vision_worker
/tmp/macau_tb_vision_worker --list-languages | grep -E 'zh-Hant|pt-|en-'
```

Required for the intended route:
- `zh-Hant` for Traditional Chinese account labels;
- a Portuguese locale such as `pt-PT` or `pt-BR` for Portuguese account labels;
- an English locale such as `en-US` for numeric columns.

The program resolves `pt` and `en` to an actually supported locale. It intentionally does not fall back from `zh-Hant` to `zh-Hans`. If your macOS version does not list a needed language, the run stops with the supported-language list rather than silently selecting Simplified Chinese.

### 4. Run two smoke tests

Chinese TB (PDF page 7):

```bash
python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_v02_p07_vision \
  --pages 7 --engine vision --vision-level accurate --ocr-langs auto
```

Portuguese TB (PDF page 10):

```bash
python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_v02_p10_vision \
  --pages 10 --engine vision --vision-level accurate --ocr-langs auto
```

For a speed comparison, run the same page into a **different** output folder using `--vision-level fast`. Do not compare OCR confidence values across Vision and Paddle as if they were calibrated to the same scale.

### 5. Run the full issue after visual checks

```bash
python macau_tb_ocr.py /path/to/anotariais-45-2026.pdf \
  --outdir output_v02_issue45 \
  --pages 7-32 --engine vision --vision-level accurate --ocr-langs auto
```

The first run compiles `vision_ocr_worker.swift` into `.vision_cache/`. Later runs reuse the compiled worker unless the Swift source changes.

## Optional PaddleOCR fallback on Apple Silicon

Only install this if you explicitly want to compare or use PaddleOCR:

```bash
python -m pip install paddlepaddle==3.3.0 \
  -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
python -m pip install -r requirements-paddle.txt
```

Then run with `--engine paddle --device cpu`. Paddle is not the v0.3 macOS default.

## Intel Mac

Vision is also a macOS framework, but supported recognition languages depend on the installed macOS Vision version. Check `--list-languages` as above. PaddlePaddle macOS x86_64 support is not assumed; use a separately supported runtime if you need the Paddle fallback.

## Troubleshooting

### `swiftc` not found

```bash
xcode-select --install
```

Then restart Terminal and check `swiftc --version`.

### Vision says `zh-Hant` is unavailable

The current macOS/Vision revision on the machine does not expose this locale for the selected recognition level. The worker deliberately will not switch to `zh-Hans`. Update macOS if appropriate, or use the optional Paddle/Tesseract route and benchmark it separately.

### Portuguese is unavailable

Run the worker with `--list-languages` and check for `pt-PT`, `pt-BR`, or another `pt-*` identifier. The worker chooses an available Portuguese locale. If none is listed, do not assume English OCR is an equivalent substitute.

### OCR is slow

- Compare `--vision-level fast` and `accurate` on the same page using separate output directories.
- The worker recognises table columns as separate regions: Traditional Chinese/Portuguese for the account-label column and English for debit/credit numeric columns.
- It uses a low minimum text-height threshold so small statement text is not discarded; increasing the threshold may speed processing but risks missing small text.
- Record wall-clock time and compare results against a manually checked sample before changing the default.

### Number format appears wrong

The parser infers number punctuation from the statement's numeric evidence, not its language. Review `number_format`, `number_format_confidence`, `*_raw`, `*_amount`, and `*_parse_status`. Set `--number-format comma_dot` or `--number-format dot_comma` only when visually confirmed. Every OCR separator recovery is flagged for review.

### Simplified Chinese appears in OCR output

`label_raw` is intentionally untouched. `label_normalized` uses OpenCC `s2hk` by default to convert common Simplified glyphs into Hong Kong Traditional forms. Check `label_normalization_status`; never replace the raw source text with the normalized label.
