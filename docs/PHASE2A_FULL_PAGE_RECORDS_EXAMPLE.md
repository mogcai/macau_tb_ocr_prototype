# Phase 2A — Full-page `documents.csv` example

This example was generated from a **3-page test fixture PDF**, not a real Gazette issue:

- page 1: TB page, OCR completed;
- page 2: non-TB page, explicitly `skipped` with `skip_reason = not_tb_page`;
- page 3: TB page, simulated OCR failure with `ocr_status = failed` and
  `error_message = "RuntimeError: fixture vision failure"`.

The companion CSV is [`PHASE2A_FULL_PAGE_RECORDS_EXAMPLE.csv`](PHASE2A_FULL_PAGE_RECORDS_EXAMPLE.csv).
It demonstrates the additive full-page fields without changing the existing V5
columns. `image_path` values are sanitised to `outputs/runs/phase2a_fixture/...`.

Relevant fields:

```text
pdf_page   ocr_status   skip_reason              error_message                     is_tb_page
1          completed                                             (empty)           True
2          skipped      not_tb_page              (empty)                           False
3          failed       (empty)                  RuntimeError: fixture vision failure  True
```

A failed page is recorded as failed, never as skipped or successful. The
full-page run report for the same fixture is:

```json
{
  "pdf_pages": 3,
  "page_records": 3,
  "tb_pages_detected": 2,
  "ocr_completed_pages": 1,
  "ocr_skipped_pages": 1,
  "ocr_failed_pages": 1,
  "ocr_page_coverage": "full"
}
```

`ocr_page_coverage = "full"` means one record exists for every PDF page; it does
not mean every page was successfully OCR'd. The failed count remains explicit.
