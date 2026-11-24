"""Adopted first-batch perimeter.

Names are the canonical_zh values from the 2026-11-22 adoption. Short names
are not official labels. The three buckets are a display perimeter: nothing
in this module adds them together.
"""
from __future__ import annotations

from typing import TypedDict


class BucketSpec(TypedDict):
    id: str
    title: str
    rule: str
    names: tuple[str, ...]


COMPARISON_DATES: tuple[str, ...] = ("2026-03-31", "2026-06-30", "2026-09-30")

LEGAL_BANKS: tuple[str, ...] = (
    "中國銀行(澳門)股份有限公司",
    "中國工商銀行(澳門)股份有限公司",
    "大豐銀行股份有限公司",
    "大西洋銀行股份有限公司",
    "澳門國際銀行股份有限公司",
    "澳門商業銀行股份有限公司",
    "華僑銀行(澳門)股份有限公司",
    "立橋銀行股份有限公司",
)

RELATED_ENTITIES: tuple[str, ...] = (
    "中國工商銀行(澳門)股份有限公司(集團)",
    "中國工商銀行股份有限公司澳門分行",
    "中國銀行股份有限公司澳門分行",
)

FOREIGN_BRANCHES: tuple[str, ...] = (
    "中國建設銀行澳門分行",
    "中國農業銀行股份有限公司澳門分行",
    "香港上海滙豐銀行有限公司澳門分行",
    "恒生銀行有限公司澳門分行",
)

BUCKETS: tuple[BucketSpec, ...] = (
    {
        "id": "legal",
        "title": "本地法人商業銀行",
        "rule": "各自獨立，不可相加，亦不可當成行業合計。",
        "names": LEGAL_BANKS,
    },
    {
        "id": "related",
        "title": "關聯報表",
        "rule": "必須分列。不可當成額外一間銀行，亦不可加入本地法人組。",
        "names": RELATED_ENTITIES,
    },
    {
        "id": "foreign_branch",
        "title": "外地銀行分行",
        "rule": "不可同本地法人商業銀行加總。",
        "names": FOREIGN_BRANCHES,
    },
)

ADOPTED_NAMES: frozenset[str] = frozenset([*LEGAL_BANKS, *RELATED_ENTITIES, *FOREIGN_BRANCHES])

NAME_BUCKET: dict[str, str] = {
    name: str(bucket["id"])
    for bucket in BUCKETS
    for name in bucket["names"]
}

MAPPING_STATUSES: tuple[str, ...] = (
    "approved",
    "proposed",
    "insert-direct",
    "unmapped",
)

# 2026-11-22 mapping-draft findings. Warnings only: not a frozen crosswalk
# and not an approved indicator list.
DRAFT_ITEM_WARNINGS: dict[str, str] = {
    "user:總計": (
        "草稿警示：user:總計 是借貸平衡總和，內含備查／對銷帳及收支行。"
        "它不是總資產，不可默默用作「佔總資產」的分母。"
    ),
    "user:AMCM存款": (
        "草稿警示：user:AMCM存款 是資產側存放於金管局的款項，不是客戶存款。"
    ),
    "user:股本": (
        "草稿警示：user:股本 與 user:營運資金 不是同一項目，不可互換，亦不可加總成資本合計。"
    ),
    "user:營運資金": (
        "草稿警示：user:營運資金 與 user:股本 不是同一項目，不可互換，亦不可加總成資本合計。"
    ),
}

DEPOSIT_PARENT_KEYS: frozenset[str] = frozenset(
    {
        "user:活期存款",
        "user:定期存款",
        "user:通知存款",
        "user:公共機構存款",
        "user:AMCM存款",
    }
)

DEPOSIT_PARENT_WARNING = (
    "草稿警示：存款父項多數是標題，金額在「澳門元／外幣」子項。"
    "大西洋銀行股份有限公司的版面常把貨幣寫在父項標籤，結構不同。父項與子項不可相加。"
)

PAGE_WARNINGS: tuple[str, ...] = (
    "可用集只計已 adopted 的報表。窗內但未 adopted 的報表只顯示為覆蓋缺口，不進入可用合計，亦不補數。",
    "比較窗只限結算日 2026-03-31、2026-06-30、2026-09-30。這不是日曆年 2025，也不是完整最近一年。2025-12-31 不在預設可用集。",
    "同名科目標籤不是自動可比。比較前要看幣別、unit_scale、reporting_scope、entity_type、statement_date 同 mapping method。",
    "mapping_status 的 approved 是 Workbench 標記，不是財務接受。proposed（常見 bilingual_exact／alias_exact）可以對錯，尤其是 OCR 變形標籤。",
    "v_unmapped = 0 只表示幾乎每行都有候選對照，不是已經可以安全對照。",
    "標準科目庫沒有已定的「總資產」「客戶存款合計」「權益合計」「淨放款」指標。具體指標尚未決定。金額與佔比都在範圍內，但本版不計算佔比。",
    "草稿警示（不是已凍結官方對照）：user:總計 不是總資產，不可默默用作佔比分母。user:AMCM存款 不是客戶存款。user:股本 與 user:營運資金 不可互換。",
    DEPOSIT_PARENT_WARNING,
    "OCR 信心、解析成功、借貸相等都不是財務核實。每行要能追到機構、期間、adopted 狀態、mapping_status 同來源標籤。",
    "第一期仍有「加總後睇澳門銀行整體情況」這個目標，但現在把已採納名單相加並不成立。本版不計算 15 間合計，亦不計算 8 間本地法人合計。",
)

PERIMETER_REASON = (
    "加總後睇澳門銀行整體情況仍然是第一期目標。"
    "現在的口徑是：8 間本地法人商業銀行各自獨立、不可相加；"
    "3 份關聯報表必須分列，不可當成額外一間銀行，亦不可加入法人組；"
    "4 間外地銀行分行不可同本地法人加總；合計與子項不可混加。"
    "因此本版不計算任何行業合計或 8 間法人合計。"
)

SHARE_BLOCK_REASON = (
    "佔比欄預留，但具體指標與分母都尚未決定。"
    "user:總計 是借貸平衡總和，不是總資產，不可默默用作分母。"
    "本版不計算任何佔比。"
)

NOT_SAFELY_COMPARABLE = (
    "本版不把任何高階指標標成已經可以安全跨行比較。"
    "候選對照只供核對。proposed 金額不是已核准規模，亦不是排名或行業合計。"
)

BALANCE_SHEET = "balance_sheet"


def bucket_for_name(canonical_zh: str) -> str | None:
    return NAME_BUCKET.get(canonical_zh)


def bucket_by_id(bucket_id: str) -> BucketSpec | None:
    for bucket in BUCKETS:
        if bucket["id"] == bucket_id:
            return bucket
    return None


def empty_mapping_counts() -> dict[str, int]:
    return {status: 0 for status in MAPPING_STATUSES}


def draft_warnings_for(item_keys: list[str]) -> list[str]:
    warnings: list[str] = []
    for key in item_keys:
        text = DRAFT_ITEM_WARNINGS.get(key)
        if text and text not in warnings:
            warnings.append(text)
        if key in DEPOSIT_PARENT_KEYS and DEPOSIT_PARENT_WARNING not in warnings:
            warnings.append(DEPOSIT_PARENT_WARNING)
    return warnings
