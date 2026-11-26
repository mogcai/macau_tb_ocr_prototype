# OCR Recognition Quality Upgrade Specification

**Project:** Macau Gazette Balancete OCR  
**Target baseline:** current `main` / v0.7.0  
**Primary OCR engine:** Apple Vision (`VNRecognizeTextRequest`)  
**Scope:** improve recognition of account labels and monetary amounts only  
**Status:** proposed implementation specification

---

## 1. Objective

Improve the OCR recognition quality of Macau bank Balancete tables, with particular attention to:

1. Traditional Chinese account names;
2. Portuguese account names, including accents and cedillas;
3. monetary digits;
4. thousands separators and decimal separators; and
5. small or low-resolution printed text.

The implementation should build on the existing Apple Vision pipeline. It should not replace the OCR engine or introduce a new OCR framework by default.

The intended outcome is better recognition of the characters actually printed in the source image. The system must continue to preserve the original OCR reading and the evidence used to produce any alternative reading.

## 2. Explicit scope boundaries

### In scope

- Preserve multiple Apple Vision recognition candidates.
- Improve Traditional Chinese and Portuguese label recognition.
- Add targeted, cell-level OCR for account labels.
- Improve targeted, cell-level OCR for debit and credit amounts.
- Benchmark image scaling and local image preprocessing.
- Improve the OCR benchmark so text, digits and punctuation can be measured independently.
- Use the uploaded `Images207.jpeg` as an additional low-resolution Chinese-table test case.

### Out of scope

Do **not** implement any of the following as part of this work:

- accounting or arithmetic validation;
- debit/credit balance checks;
- printed-total reconciliation;
- Data Verification or financial verification workflows;
- changes to V3 verification, adoption, publication or Workbench workflows;
- Tesseract as a second OCR engine or as a source of OCR truth;
- automatic correction of an OCR amount merely to make it look financially plausible;
- automatic replacement of source OCR text with a dictionary entry that was not actually returned by OCR.

The user will handle data verification manually. This specification is solely about recognition quality.

Existing optional Tesseract code may remain untouched for compatibility, but this upgrade must not call it, depend on it, or use its results in any benchmark or candidate-selection logic.

## 3. Existing implementation to preserve

The current implementation already has several useful foundations:

- Apple Vision is the default engine on macOS.
- Traditional Chinese, Portuguese and English/numeric regions are routed separately.
- The account-label, debit and credit regions are recognised separately.
- Numeric OCR uses English recognition with language correction disabled.
- The original raw OCR text, confidence and bounding boxes are exported.
- The amount parser supports `comma_dot` and `dot_comma` based on page evidence, not document language.
- An amount second pass already crops suspicious cells and reads them at 3x.
- Ground-truth tooling already measures label and amount recognition.

Do not remove or weaken these behaviours while implementing the upgrade.

Relevant current files:

- `vision_ocr_worker.swift`
- `macau_tb_ocr.py`
- `MACOS_VISION_OCR.md`
- `ground_truth/issue45_2026/`
- `tools/gt_build_sheet.py`
- `tools/gt_score.py`

## 4. Phase 0 — establish a valid v0.7.0 baseline

Before changing OCR behaviour, run the current `main` branch on the target macOS machine.

### 4.1 Existing benchmark pages

Use the existing verified Issue 45 ground truth pages:

- page 7 — Traditional Chinese;
- page 9 — Traditional Chinese;
- page 10 — Portuguese;
- page 15 — Portuguese;
- page 17 — bilingual;
- page 29 — layout with an additional account-code column.

Create a fresh OCR run with a new output directory. Do not use the historical `score_report.json` as the v0.7.0 baseline: that report records an older run and must not be treated as a measurement of the current implementation.

### 4.2 New image case: `Images207.jpeg`

Add the uploaded 686 × 1,342 JPEG as a separate OCR test fixture. It is useful for measuring small Traditional Chinese labels and amount characters at relatively low source resolution.

The test harness should be able to pass a standalone JPEG/PNG directly to the OCR page-processing functions. This is a test-entry convenience and must not change PDF issue-import behaviour.

Create manually checked ground truth for this image before using it to claim an accuracy improvement. Ground truth must record what is visibly printed, not what a label or amount is expected to be.

### 4.3 Baseline metrics

Record at least:

| Metric | Purpose |
|---|---|
| Traditional Chinese CER | Character error rate for Chinese labels |
| Portuguese CER | Character error rate for Portuguese labels |
| Label exact-match rate | Full account-label match |
| Numeric character accuracy | Correct digit characters, measured separately from punctuation |
| Amount exact-match rate | Entire canonical amount matches ground truth |
| Separator accuracy | Correct comma, dot and decimal placement |
| Top-5 candidate recall | Whether the correct reading appears among Vision's first five candidates |
| Processing time | Cost of additional recognition passes |

Report metrics overall and by page/language. Do not compare confidence values from different engines or treat confidence as a calibrated probability.

## 5. Phase 1 — preserve multiple Apple Vision candidates

### 5.1 Current limitation

`vision_ocr_worker.swift` currently requests only the first candidate:

```swift
observation.topCandidates(1).first
```

This discards alternatives that may contain the correct character or digit.

### 5.2 Required change

For each Vision observation, request and preserve up to five candidates using `topCandidates(5)`.

Each candidate should retain:

- candidate text;
- candidate confidence;
- candidate order/rank.

The observation should continue to retain its existing region, language, recognition level and bounding box.

### 5.3 Backward compatibility

Keep the existing top-level fields:

- `text` — the original first/top candidate;
- `confidence` — confidence of that first/top candidate;
- `x0`, `y0`, `x1`, `y1` — existing coordinates.

Add candidate data as an additional field, for example `candidates`. Do not change the meaning of `text` or silently replace it with a re-ranked candidate.

Export the candidate list in `ocr_raw.csv` (for example as JSON in `candidates_json`) or in a dedicated `ocr_candidates.csv`. Existing consumers of `ocr_raw.csv` must continue to work.

### 5.4 Candidate selection and re-ranking

A later selection layer may rank only candidates actually returned by Vision. It must never invent a new text string.

For labels, ranking may consider:

- Vision candidate confidence;
- exact matches against the project's curated Macau-bank label vocabulary;
- Traditional Chinese character variants;
- Portuguese accents, cedillas and case-folded comparison;
- consistency between the detected script and the selected recognition language.

For amounts, ranking may consider:

- Vision candidate confidence;
- digits and punctuation structure;
- the page's independently inferred number-format profile;
- the expected two decimal places used by the printed statement format, where evidenced by the page.

Dictionary or format evidence is a ranking hint only. It must not force a candidate that Vision did not return, and it must not be used to manufacture missing digits.

Keep the first-pass top candidate and any selected alternative separately identifiable in the output. Record the selected candidate rank and selection reason.

## 6. Phase 2 — improve account-label recognition

### 6.1 Label-cell second pass

Keep the existing full label-column pass. Add a targeted second pass for individual label cells, using the current row/cell geometry as the crop source.

For each eligible row:

1. determine the label-column horizontal bounds from the detected table separators;
2. determine the row's vertical bounds from the existing row/box geometry;
3. crop the label cell with a small configurable padding;
4. preserve multi-line labels rather than clipping a second line;
5. run Vision Accurate on the crop with the appropriate language profile;
6. preserve the second-pass text, candidates, confidence and crop coordinates.

Do not merge two different account rows merely because their labels look similar. Do not use a dictionary match to change row boundaries.

### 6.2 When to run the second pass

Use targeted label re-reading when one or more of these conditions applies:

- first-pass confidence is below a configurable threshold;
- the label is empty but the label cell contains visible ink;
- the candidate list has close competing readings;
- a label is unusually short, malformed or contains suspicious mixed scripts;
- the first-pass label has a low-confidence match against the existing vocabulary.

Do not re-read every label by default until benchmarks show that the extra processing time is worthwhile.

### 6.3 Custom vocabulary

Test Apple Vision's `customWords` support for label/header recognition, subject to the macOS SDK and runtime availability of the target machine.

Use separate curated vocabulary lists for:

- Traditional Chinese bank/account terminology;
- Portuguese bank/account terminology;
- bank names and recurring Macau financial-institution names.

Only use the vocabulary on text regions. Never pass numeric amount cells through language correction or label dictionaries.

Important safeguards:

- A vocabulary entry is a recognition hint, not proof that the printed text contains that term.
- New or unmatched labels must remain possible.
- Do not force a fuzzy dictionary match into the OCR output.
- Benchmark `usesLanguageCorrection` both enabled and disabled by language. The existing documentation notes that it had little effect on Traditional Chinese and could affect Portuguese accents/typos; retain whichever setting performs better on held-out ground truth.

### 6.4 Preserve raw and selected readings

Keep the first-pass reading immutable. Add distinct fields for the second pass and selected OCR result, such as:

- `label_raw` — existing first-pass top candidate;
- `label_candidates_json` — first-pass candidate list;
- `label_second_pass_raw` — targeted second-pass top candidate;
- `label_second_pass_candidates_json` — second-pass candidates;
- `label_ocr_selected` — selected OCR candidate, if a selection is justified;
- `label_ocr_selected_source` — `first_pass`, `second_pass` or `candidate_rerank`;
- `label_ocr_selected_rank` — candidate rank, when applicable.

If no alternative is demonstrably better, retain the first-pass candidate as the selected result. Do not overwrite `label_raw`.

## 7. Phase 3 — improve monetary amount recognition

### 7.1 Keep current numeric profile

Retain the existing numeric recognition policy:

- language: English (`en-US` or the supported English locale);
- recognition level: Accurate;
- language correction: off;
- no Chinese or Portuguese language assumption for number punctuation.

Do not add label vocabulary to numeric requests.

### 7.2 Improve cell-level amount re-reading

Keep the existing amount second pass, but expand it from a fixed 3x-only crop to a measured cell-level strategy.

For each selected amount cell:

1. use the debit/credit column bounds and the row's vertical bounds;
2. where a first-pass amount bounding box exists, use it to tighten the crop without clipping punctuation;
3. where the first pass is blank, use the full row-by-amount-column cell;
4. retain sufficient horizontal padding so the first/last digit and punctuation are not clipped;
5. preserve the original image crop and all transformed variants used for OCR.

### 7.3 Compare scale variants

Benchmark the following scale variants on the same ground-truth cells:

- 1x (original crop);
- 2x;
- 3x (current baseline);
- 4x.

Compare interpolation methods such as `INTER_CUBIC` and `INTER_LANCZOS4` where appropriate. Do not assume that larger is always better. Choose a default using amount exact-match and separator accuracy, not confidence alone.

To control runtime, do not run every scale on every cell in production. A practical staged approach is:

- first pass: existing full-column recognition;
- targeted pass: one selected scale for eligible cells;
- optional alternate variant: only when the targeted result remains uncertain or the candidate readings disagree.

### 7.4 Local image preprocessing experiments

Benchmark these crop variants independently:

- original grayscale;
- local contrast enhancement (for example CLAHE);
- adaptive thresholding.

Keep the original crop as the reference. Do not enable global page-level contrast/grayscale preprocessing by default: the existing project notes that page-wide contrast processing changed thousands separators and affected Chinese labels.

Avoid aggressive erosion, dilation or sharpening unless a ground-truth test demonstrates an improvement. These operations can destroy decimal points, commas and thin digit strokes.

### 7.5 Numeric candidate handling

Preserve up to five Vision candidates for each amount observation and for each targeted amount-cell pass.

Candidate comparison may use the page's number-format evidence and valid grouping structure to rank candidates. The rules must remain conservative:

- do not add or delete digits;
- do not silently convert `O` to `0`, `I` to `1`, `S` to `5` or similar characters;
- if a character substitution is proposed by existing parsing rules, retain the original OCR string and mark the derived value as a recognition/normalization candidate;
- do not silently replace a confident first-pass amount with a disagreeing second-pass result;
- retain all conflicting readings and their provenance.

The existing `parse_amount()` support for `comma_dot` and `dot_comma` should be preserved. Number-format inference must continue to use page-level numeric evidence, never the document language.

### 7.6 Per-region minimum text height

The Swift worker currently supplies one `minimumTextHeight` value to all tasks. Add an optional per-region override to `RegionTask`, with the current global value retained as fallback.

Benchmark at least these values for small text:

- `0.004` (current default);
- `0.003`;
- `0.002`.

Run separate tests for label and amount crops. A lower threshold may detect smaller text but may also increase noise and processing time. Do not change the production default until the GT results justify it.

## 8. Image geometry and input quality

Recognition improvements depend on correct crops. Add lightweight diagnostics for:

- source image dimensions;
- median label glyph/box height;
- median amount glyph/box height;
- crop dimensions and scale factor;
- detected label/debit/credit boundaries;
- whether the crop touches or intersects a table rule.

For the new standalone JPEG/PNG test entry, reuse the same OCR functions as the PDF pipeline rather than create a separate OCR implementation.

If deskewing is tested, do it on a crop or maintain a precise transform back to original-image coordinates. Do not let a transformed image invalidate the source bounding boxes saved in OCR evidence.

Do not simply lower the existing embedded-image dimension threshold for all PDF pages. First measure actual glyph height and confirm that the candidate image is a Balancete table. The uploaded JPEG is a test case, not evidence that every small embedded PDF image should automatically be accepted.

## 9. Output and provenance requirements

The OCR output must retain enough information to reproduce why a reading was selected.

For each first-pass and targeted-pass result, retain as applicable:

- source image path;
- original crop coordinates;
- preprocessing variant;
- scale factor and interpolation method;
- OCR language;
- requested and actual recognition level;
- `minimumTextHeight` used;
- `usesLanguageCorrection` setting;
- candidate texts and confidence values;
- selected candidate rank and selection reason;
- elapsed time.

Keep existing columns and their meanings backward-compatible. Add new fields rather than repurpose existing raw fields.

No V3 database, statement verification, adoption, publication or Workbench schema changes are required for this specification.

## 10. Ground-truth benchmark and acceptance criteria

### 10.1 Test set

Use:

1. verified Issue 45 pages 7, 9, 10, 15, 17 and 29;
2. manually labelled `Images207.jpeg`;
3. additional new-issue pages after the initial experiment, to test generalisation beyond Issue 45.

The existing Issue 45 label lexicon was seeded from the same ground truth. Therefore, results using that lexicon on those same pages are in-sample and must not be presented as proof of cross-issue performance. Keep at least one new-issue test set out of vocabulary tuning.

### 10.2 Required comparisons

Every change to candidate handling, `customWords`, language correction, scaling, preprocessing or minimum text height must be compared against the v0.7.0 baseline on the same macOS machine and same source images.

Report:

- Chinese CER and exact match;
- Portuguese CER and exact match;
- numeric character accuracy;
- debit amount exact match;
- credit amount exact match;
- separator accuracy;
- top-5 candidate recall;
- number of cells improved, unchanged and made worse;
- processing-time increase.

### 10.3 Acceptance rules

A change is accepted only when:

1. it improves the targeted OCR metric on verified ground truth;
2. it does not cause a material regression on other tested pages/languages;
3. amount exact-match and separator accuracy do not regress;
4. original raw OCR and crop provenance remain available;
5. the added runtime is proportionate to the measured gain;
6. regression tests cover the new behaviour.

Do not claim a general accuracy improvement from the six existing Issue 45 pages alone. Confirm it on `Images207.jpeg` and at least one held-out/new-issue sample.

## 11. Suggested implementation order

### Phase A — candidate preservation

- Extend the Swift observation output with top-five candidates.
- Keep the existing `text` and `confidence` fields as candidate 1.
- Export candidate lists without breaking current CSV consumers.
- Add unit tests for candidate serialization and backward compatibility.

### Phase B — label recognition

- Add label-cell crop and targeted Vision second pass.
- Add Traditional Chinese and Portuguese custom-word experiments.
- Add candidate ranking restricted to Vision-returned candidates.
- Keep first-pass raw text unchanged.

### Phase C — amount recognition

- Extend amount-cell re-reading to compare 1x/2x/3x/4x in the benchmark.
- Test local contrast and adaptive-threshold variants.
- Preserve multiple candidates and punctuation exactly.
- Select the least costly strategy that improves verified amount recognition.

### Phase D — benchmark and tune

- Re-run the existing six-page ground truth.
- Add manually checked `Images207.jpeg` ground truth.
- Test a held-out new-issue sample.
- Document the winning settings and the measured trade-offs.

Do not combine all image transformations and recognition settings into one large change. Keep each experiment independently measurable and reversible.

## 12. Definition of done

This OCR-quality upgrade is complete when:

- Apple Vision top-five candidates are preserved;
- Traditional Chinese and Portuguese label-cell second-pass recognition is available;
- targeted amount-cell recognition can benchmark and use measured scale/preprocessing settings;
- original first-pass raw OCR is never overwritten;
- number-format parsing remains page-evidence-based;
- the new candidate and crop provenance is exported;
- existing tests pass and new tests cover the changed behaviour;
- benchmark reports compare the new run against a freshly measured v0.7.0 baseline;
- improvements are demonstrated on existing GT, `Images207.jpeg`, and held-out data;
- no Data Verification, accounting validation, Tesseract dependency or V3/Workbench changes have been introduced.

---

## 13. Developer notes

Before implementation, read `AGENTS.md`, `SKILL.md`, `README_AGENT.md` and `MACOS_VISION_OCR.md` and follow their existing data-integrity requirements.

The implementation should be incremental. Do not refactor unrelated OCR ingestion, V3 database, catalogue, mapping, review or publication code as part of this task.
