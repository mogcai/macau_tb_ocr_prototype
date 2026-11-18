#!/usr/bin/env python3
"""Build the standard label lexicon from human-verified GT labels.

The lexicon is a seed, not a closed list: it is generated from verified labels,
groups case/accent/script variants, and can be edited by hand to add forms.

Usage::

    python tools/build_label_lexicon.py \
        ground_truth/issue45_2026/gt_rows.csv \
        --out labels/label_lexicon.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

CHILD_RE = re.compile(r"^[\-–—•·]\s*")
ZH_VARIANTS = str.maketrans({
    "賬": "帳",
    "帐": "帳",
    "劵": "券",
    "産": "產",
    "価": "價",
    "歴": "歷",
})


def norm(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "").replace("\u3000", " ").strip()


def has_chinese(text: str) -> bool:
    return any("\u3400" <= ch <= "\u9fff" for ch in text)


def strip_child(text: str) -> str:
    return CHILD_RE.sub("", norm(text)).strip()


def latin_key(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    ascii_text = decomposed.encode("ascii", "ignore").decode("ascii")
    # Punctuation varies between forms (comma vs period); it is not part of
    # the concept key.
    ascii_text = re.sub(r"[^0-9A-Za-z\s]", " ", ascii_text)
    # Spacing between words differs across forms ("A VALES" vs "AVALES").
    return re.sub(r"\s+", "", ascii_text).casefold()


def key_for(text: str, language: str) -> str:
    body = strip_child(text)
    if language == "zh-Hant":
        body = body.translate(ZH_VARIANTS)
        body = re.sub(r"[()（）〔〕【】\[\]、,，]", "", body)
        return re.sub(r"\s+", "", body)
    return latin_key(body)


def language_for(text: str) -> str:
    return "zh-Hant" if has_chinese(text) else "pt"


def choose_canonical(variants: Counter[str]) -> str:
    """Prefer a mixed-case form over an all-caps form, then frequency."""
    mixed = Counter({k: v for k, v in variants.items()
                     if k != k.upper() or not any(ch.isalpha() for ch in k)})
    pool = mixed or variants
    return max(pool.items(), key=lambda item: (item[1], len(item[0])))[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="由已核對 GT 建立標準標籤詞表")
    parser.add_argument("gt_rows", nargs="+", help="一個或多個 gt_rows.csv")
    parser.add_argument("--out", default="labels/label_lexicon.csv")
    args = parser.parse_args()

    groups: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    child_flag: dict[tuple[str, str], bool] = {}
    for path in args.gt_rows:
        with open(path, newline="", encoding="utf-8-sig") as handle:
            for row in csv.DictReader(handle):
                if str(row.get("verified", "")).strip() not in {"1", "true", "yes", "y"}:
                    continue
                label = norm(row.get("label_gt", ""))
                if not label:
                    continue
                language = language_for(strip_child(label))
                key = key_for(label, language)
                if not key:
                    continue
                group = (language, key)
                groups[group][label] += 1
                child_flag[group] = child_flag.get(group, False) or bool(
                    CHILD_RE.match(label)
                )

    records = []
    counters = defaultdict(int)
    for (language, key), variants in sorted(groups.items()):
        counters[language] += 1
        canonical = choose_canonical(variants)
        body = strip_child(canonical)
        if language == "zh-Hant":
            body = body.translate(ZH_VARIANTS).replace("〔", "(").replace("〕", ")")
        level = "child" if child_flag[(language, key)] else "parent"
        aliases = sorted({v for v in variants if v != canonical})
        records.append({
            "label_id": f"{'zh' if language == 'zh-Hant' else 'pt'}_{counters[language]:03d}",
            "label_std": body,
            "language": language,
            "level": level,
            "aliases": "|".join(aliases),
            "note": "",
        })

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["label_id", "label_std", "language", "level", "aliases", "note"]
        )
        writer.writeheader()
        writer.writerows(records)
    print(f"wrote {out}: {len(records)} labels "
          f"({sum(1 for r in records if r['level'] == 'child')} child entries)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
