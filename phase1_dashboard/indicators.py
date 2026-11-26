"""Display catalogue for the planned financial indicators.

This is **not** an approved indicator definition file. It mirrors the P0/P1
items in ``docs/MACAU_BANKING_DASHBOARD_DESIGN_v0.2.md`` so the initial
dashboard can explain what is missing. No value is calculated from it.

A real definition lives in ``analytics/config/indicator_catalogue.yml`` after
Phase A approval: item keys, normal balance side, row rules, period basis,
quality threshold, approvals and effective date.
"""
from __future__ import annotations

from typing import Any

INDICATOR_GROUPS: tuple[dict[str, str], ...] = (
    {
        "id": "balance_sheet",
        "title": "A. 規模及資產負債表",
        "note": "期末存量。需要已審批的標準科目組合及 row rule。",
    },
    {
        "id": "structure",
        "title": "B. 資產負債結構比例",
        "note": "分子與分母必須使用同一 report scope、幣別及小數位。",
    },
    {
        "id": "profitability",
        "title": "C. 盈利及營運效率",
        "note": "期間流量。period basis（季度／YTD／年度）未確定前不計算。",
    },
    {
        "id": "regulatory",
        "title": "D. 風險及監管指標",
        "note": "需要監管申報資料，不由一般 Balancete 科目推算。",
    },
)

CORE_INDICATORS: tuple[dict[str, Any], ...] = (
    {
        "id": "total_assets",
        "name_zh": "總資產",
        "group": "balance_sheet",
        "priority": "P0",
        "definition": "期末總資產",
        "needs": "已確認的總資產標準項，或經審批的資產明細集合",
    },
    {
        "id": "total_liabilities",
        "name_zh": "總負債",
        "group": "balance_sheet",
        "priority": "P0",
        "definition": "期末總負債",
        "needs": "已確認負債合計；不可與權益混合",
    },
    {
        "id": "total_equity",
        "name_zh": "股東權益",
        "group": "balance_sheet",
        "priority": "P0",
        "definition": "期末權益合計",
        "needs": "股本、儲備、保留溢利等正確組合",
    },
    {
        "id": "customer_loans",
        "name_zh": "客戶貸款",
        "group": "balance_sheet",
        "priority": "P0",
        "definition": "客戶貸款總額（gross / net 分開）",
        "needs": "區分同業貸款、客戶貸款及減值準備",
    },
    {
        "id": "customer_deposits",
        "name_zh": "客戶存款",
        "group": "balance_sheet",
        "priority": "P0",
        "definition": "客戶存款合計",
        "needs": "排除 AMCM 存款；分清同業、公共機構及客戶存款",
    },
    {
        "id": "asset_growth",
        "name_zh": "資產增長",
        "group": "balance_sheet",
        "priority": "P0",
        "definition": "本期總資產 ÷ 上期總資產 − 1",
        "needs": "兩期 perimeter、scope 及科目定義一致",
    },
    {
        "id": "cash_central_bank",
        "name_zh": "現金及央行結存",
        "group": "balance_sheet",
        "priority": "P1",
        "definition": "現金及符合定義的央行結存",
        "needs": "明確 AMCM 存款是否納入",
    },
    {
        "id": "interbank_assets",
        "name_zh": "同業資產",
        "group": "balance_sheet",
        "priority": "P1",
        "definition": "對銀行及金融機構的資產",
        "needs": "與客戶貸款分開",
    },
    {
        "id": "securities",
        "name_zh": "證券投資",
        "group": "balance_sheet",
        "priority": "P1",
        "definition": "合資格債券／證券投資",
        "needs": "明確交易、投資及重估項目範圍",
    },
    {
        "id": "loan_to_deposit",
        "name_zh": "貸款／存款比率",
        "group": "structure",
        "priority": "P1",
        "definition": "客戶貸款 ÷ 客戶存款",
        "needs": "客戶貸款及客戶存款的已審批定義",
    },
    {
        "id": "loans_to_assets",
        "name_zh": "貸款／總資產",
        "group": "structure",
        "priority": "P1",
        "definition": "客戶貸款 ÷ 總資產",
        "needs": "分母必須是真正總資產，不是 user:總計",
    },
    {
        "id": "deposits_to_assets",
        "name_zh": "存款／總資產",
        "group": "structure",
        "priority": "P1",
        "definition": "客戶存款 ÷ 總資產",
        "needs": "客戶存款及總資產的已審批定義",
    },
    {
        "id": "equity_to_assets",
        "name_zh": "權益／總資產",
        "group": "structure",
        "priority": "P1",
        "definition": "股東權益 ÷ 總資產",
        "needs": "不是監管資本充足率",
    },
    {
        "id": "interest_income",
        "name_zh": "利息收入",
        "group": "profitability",
        "priority": "P1",
        "definition": "期間利息收入",
        "needs": "已確認 income statement item 及期間 basis",
    },
    {
        "id": "net_interest_income",
        "name_zh": "淨利息收入",
        "group": "profitability",
        "priority": "P1",
        "definition": "利息收入 − 利息支出",
        "needs": "同一期間、同一 scope",
    },
    {
        "id": "operating_expenses",
        "name_zh": "營運開支",
        "group": "profitability",
        "priority": "P1",
        "definition": "按審批定義的營運開支項合計",
        "needs": "不可混入資產負債表項目",
    },
    {
        "id": "net_profit",
        "name_zh": "稅後淨利潤",
        "group": "profitability",
        "priority": "P1",
        "definition": "期間稅後淨利潤",
        "needs": "釐清 YTD / quarter / annual basis",
    },
    {
        "id": "cost_to_income",
        "name_zh": "Cost-to-Income",
        "group": "profitability",
        "priority": "P1",
        "definition": "營運開支 ÷ 營運收入",
        "needs": "分子、分母定義一致；處理零分母",
    },
    {
        "id": "roa",
        "name_zh": "ROA",
        "group": "profitability",
        "priority": "P1",
        "definition": "年化淨利潤 ÷ 平均總資產",
        "needs": "可靠期間流量及平均資產",
    },
    {
        "id": "roe",
        "name_zh": "ROE",
        "group": "profitability",
        "priority": "P1",
        "definition": "年化淨利潤 ÷ 平均股東權益",
        "needs": "可靠期間流量及平均權益",
    },
    {
        "id": "npl_ratio",
        "name_zh": "不良貸款率",
        "group": "regulatory",
        "priority": "P2",
        "definition": "不良貸款 ÷ 客戶貸款總額",
        "needs": "不良貸款分類金額（監管資料）",
    },
    {
        "id": "provision_coverage",
        "name_zh": "撥備覆蓋率",
        "group": "regulatory",
        "priority": "P2",
        "definition": "撥備 ÷ 不良貸款",
        "needs": "不良貸款及對應減值／撥備",
    },
    {
        "id": "capital_adequacy",
        "name_zh": "資本充足率",
        "group": "regulatory",
        "priority": "P2",
        "definition": "監管資本 ÷ 風險加權資產",
        "needs": "監管申報資料，不由股本／權益推算",
    },
    {
        "id": "lcr",
        "name_zh": "流動性覆蓋率 (LCR)",
        "group": "regulatory",
        "priority": "P2",
        "definition": "HQLA ÷ 30 日淨現金流出",
        "needs": "專門監管數據",
    },
    {
        "id": "nsfr",
        "name_zh": "淨穩定資金比率 (NSFR)",
        "group": "regulatory",
        "priority": "P2",
        "definition": "可用穩定資金 ÷ 所需穩定資金",
        "needs": "專門監管數據",
    },
)


def _blockers(indicator: dict[str, Any], *, publication_count: int, approved_rows: int) -> list[str]:
    blockers = ["未有事業定義：indicator catalogue 尚未建立／審批。"]
    if publication_count == 0:
        blockers.append("未有已發布報表；正式 KPI 須經 publication workflow。")
    if approved_rows == 0:
        blockers.append("未有 approved mapping 可作為指標來源。")
    else:
        blockers.append("未確認所需標準科目、借貸方向及 row rule。")
    if indicator["group"] in {"balance_sheet", "structure"}:
        blockers.append("行業 perimeter 未審批；只可有已覆蓋機構小計，不可稱全澳總計。")
    if indicator["group"] == "profitability":
        blockers.append("statement period basis（季度／YTD／年度）尚未記錄。")
    if indicator["group"] == "regulatory":
        blockers.append("未接入監管申報資料。")
    return blockers


def build_indicator_view(
    *, publication_count: int, publication_total: int, approved_rows: int, proposed_rows: int
) -> dict[str, Any]:
    status = {
        "publication_count": publication_count,
        "publication_total": publication_total,
        "approved_rows": approved_rows,
        "proposed_rows": proposed_rows,
        "approved_definitions": 0,
        "approved_perimeter": False,
        "enabled_count": 0,
        "total_count": len(CORE_INDICATORS),
    }
    indicators = []
    for indicator in CORE_INDICATORS:
        indicators.append({
            **indicator,
            "group_title": next(
                group["title"] for group in INDICATOR_GROUPS
                if group["id"] == indicator["group"]
            ),
            "enabled": False,
            "status": "未啟用",
            "blockers": _blockers(
                indicator,
                publication_count=publication_count,
                approved_rows=approved_rows,
            ),
        })
    groups = []
    for group in INDICATOR_GROUPS:
        groups.append({
            **group,
            "indicators": [
                indicator for indicator in indicators
                if indicator["group"] == group["id"]
            ],
        })
    return {
        "status": status,
        "groups": groups,
        "headline": "正式金融指標未啟用",
        "reason": (
            "dashboard 目前只展示已採納報表的候選行觀察。"
            "未有已審批 indicator catalogue、publication 及 entity perimeter，"
            "所以不計算總資產、客戶存款、比率、排名或行業合計。"
        ),
        "enable_conditions": [
            "審批澳門銀行業統計 perimeter，列明應納入及應排除的機構。",
            "審批核心科目 mapping 及每個指標的 item keys、借貸方向、row type 規則。",
            "補上 income statement 的 period basis（季度／YTD／年度）。",
            "完成 publication workflow，並設定每個指標所需 publication quality。",
            "建立可追溯的 aggregation engine；行業比率用 ratio-of-sums。",
        ],
    }
