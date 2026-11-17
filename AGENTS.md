# Instructions for AI Agents

When asked to process Macau Government Gazette bank/financial-institution Balancete PDFs in this project:

1. Read `SKILL.md` before running or modifying the program.
2. Use `README_AGENT.md` for the concise command sequence and `README_zh_HK.md` for human setup guidance.
3. Follow the non-negotiable data-integrity rules in `SKILL.md`: inspect first, never overwrite source PDFs, use a fresh output directory, preserve raw OCR text and provenance, and route uncertain results for review.
4. Do not report OCR accuracy or financial reconciliation unless supported by a manually verified ground-truth dataset and explicit calculations.
5. Do not change OCR models, thresholds, parsing rules, or source code during a processing run without the user's instruction. Propose changes separately and validate them with tests and a fixed benchmark.
