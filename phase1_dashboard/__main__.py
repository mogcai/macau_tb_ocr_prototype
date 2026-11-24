"""Start the phase-1 dashboard on 127.0.0.1 only.

    python -m phase1_dashboard --db /path/to/macau_balancete_v3.sqlite
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from phase1_dashboard.db import DashboardDataError, connect_readonly

LOOPBACK = "127.0.0.1"
ENV_DB = "PHASE1_DASHBOARD_DB"


def resolve_host(host: str | None) -> str:
    """Bind only to loopback. Any other address is refused."""
    cleaned = (host or "").strip().lower()
    if cleaned in {"127.0.0.1", "localhost", "::1"}:
        return LOOPBACK
    raise SystemExit(
        f"拒絕綁定 {host!r}。第一期檢視只可以在 127.0.0.1 運行，"
        "不可以對區域網或公開地址開放。"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="第一期本機檢視：只讀 V3，只綁定 127.0.0.1。"
    )
    parser.add_argument(
        "--db",
        default=os.environ.get(ENV_DB),
        help=f"V3 SQLite 路徑。亦可用環境變數 {ENV_DB}。",
    )
    parser.add_argument("--host", default=LOOPBACK)
    parser.add_argument("--port", type=int, default=8010)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    host = resolve_host(args.host)
    if args.port < 1 or args.port > 65535:
        parser.error("port 必須在 1 到 65535 之間")
    if not args.db:
        parser.error(
            f"請用 --db 或環境變數 {ENV_DB} 指定本機 V3 SQLite 路徑。"
            "不要把路徑寫進程式。"
        )
    db_path = Path(args.db).expanduser()
    try:
        connection = connect_readonly(db_path)
    except DashboardDataError as exc:
        raise SystemExit(str(exc)) from exc
    connection.close()
    from phase1_dashboard.app import create_app

    app = create_app(db_path)
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit(
            "需要 uvicorn。請先執行：python -m pip install -r requirements-workbench.txt"
        ) from exc
    print("第一期本機檢視（只讀，沒有登入）")
    print(f"資料庫：{db_path}")
    print(f"http://{host}:{args.port}/")
    uvicorn.run(app, host=host, port=args.port, log_level="info")


if __name__ == "__main__":
    main(sys.argv[1:])
