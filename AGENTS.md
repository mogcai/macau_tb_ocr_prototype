# Instructions for AI Agents

Before running or modifying this project:

1. Read `SKILL.md` and `README_AGENT.md`. For a Mac install, read `MACOS_SETUP.md`.
2. Inspect each new PDF before OCR. Use a new output directory per run; never overwrite the source PDF or prior result folders.
3. On macOS, use Apple Vision by default. Check `zh-Hant`, Portuguese, and English language availability on the actual machine. Never silently use Simplified Chinese when Traditional Chinese is unavailable.
4. Preserve `label_raw`, `debit_raw`, `credit_raw`, source images, bounding boxes, and OCR evidence. Any normalized value must live in a separate column.
5. Number punctuation is inferred from page-level evidence, not the page language. Never infer `dot_comma` merely because text is Portuguese.
6. Separator repair is allowed only when digit groups remain structurally valid and page-level format evidence supports it. Recovered values must be marked for review. Never invent missing digits or suppress an ambiguity.
7. Do not mark any amount financially verified based only on OCR confidence, parser success, or debit/credit totals. Human verification needs source-image comparison.
8. Run unit tests after code changes. Benchmark changes against manually labelled pages; do not report OCR accuracy or speed improvements without those measurements on the target Mac.
9. Do not change OCR engines, models, thresholds, locale routes, number parsing policies or source code during a user processing run without explicit instruction.
