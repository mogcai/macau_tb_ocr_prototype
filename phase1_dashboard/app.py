"""Local phase-1 inspection app. Read-only. No authentication."""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from phase1_dashboard.db import DashboardDataError, friendly_db_error, open_readonly
from phase1_dashboard.present import ScopeError, aggregate_placeholder
from phase1_dashboard.scope import (
    BUCKETS,
    COMPARISON_DATES,
    MAPPING_STATUSES,
    NOT_SAFELY_COMPARABLE,
    PAGE_WARNINGS,
)
from phase1_dashboard import service

HERE = Path(__file__).resolve().parent
TEMPLATES = Jinja2Templates(directory=str(HERE / "templates"))
ALLOWED_HOST_NAMES = frozenset({"127.0.0.1", "localhost"})


def host_header_allowed(value: str | None) -> bool:
    """Allow only loopback names, with at most one numeric port.

    DNS rebinding can point a public name at 127.0.0.1. The socket is already
    loopback-only; this rejects any Host header that is not 127.0.0.1 or
    localhost, so a rebound name cannot read the unauthenticated data.
    """
    if value is None:
        return False
    host = value.strip().lower()
    if not host or any(ch.isspace() for ch in host):
        return False
    if any(ch in host for ch in "@/\\?#[]"):
        return False
    name, separator, port = host.rpartition(":")
    if separator:
        if not port.isdigit() or not 1 <= int(port) <= 65535:
            return False
        hostname = name
    else:
        hostname = host
    return hostname in ALLOWED_HOST_NAMES


def create_app(db_path: Path | str) -> FastAPI:
    app = FastAPI(title="澳門銀行第一期本機檢視", version="0.1.0")
    app.state.db_path = str(Path(db_path).expanduser())
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.middleware("http")
    async def no_store(request: Request, call_next):
        if not host_header_allowed(request.headers.get("host")):
            return JSONResponse(
                {
                    "error": "Host 只接受 127.0.0.1 或 localhost。",
                    "read_only": True,
                },
                status_code=400,
            )
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Phase1-Dashboard"] = "read-only"
        return response

    @app.exception_handler(DashboardDataError)
    async def data_error(_: Request, exc: DashboardDataError) -> JSONResponse:
        return JSONResponse({"error": str(exc), "read_only": True}, status_code=400)

    @app.exception_handler(sqlite3.DatabaseError)
    async def sqlite_error(_: Request, exc: sqlite3.DatabaseError) -> JSONResponse:
        return JSONResponse(
            {"error": friendly_db_error(exc), "read_only": True},
            status_code=400,
        )

    @app.exception_handler(ScopeError)
    async def scope_error(_: Request, exc: ScopeError) -> JSONResponse:
        return JSONResponse(
            {"error": str(exc), "amounts": None, "computed": False},
            status_code=400,
        )

    def common() -> dict[str, Any]:
        return {
            "window": COMPARISON_DATES,
            "buckets": BUCKETS,
            "page_warnings": PAGE_WARNINGS,
            "not_comparable": NOT_SAFELY_COMPARABLE,
            "mapping_statuses": MAPPING_STATUSES,
            "aggregate": aggregate_placeholder(),
        }

    def render(request: Request, name: str, **extra: Any):
        return TEMPLATES.TemplateResponse(
            request, name, {**common(), **extra}
        )

    @app.get("/health")
    def health() -> dict[str, Any]:
        with open_readonly(app.state.db_path) as connection:
            connection.execute("SELECT 1 FROM reporting_entities LIMIT 1").fetchall()
        return {
            "ok": True,
            "read_only": True,
            "authentication": False,
            "host_policy": "127.0.0.1",
            "v5": False,
            "window": list(COMPARISON_DATES),
            "amounts_are_financially_verified": False,
        }

    @app.get("/")
    def home_page(
        request: Request,
        bucket: str | None = None,
        statement_date: str | None = None,
        item_key: str | None = None,
        entity_id: str | None = None,
    ):
        with open_readonly(app.state.db_path) as connection:
            model = service.chart_home(
                connection,
                bucket_id=bucket,
                statement_date=statement_date,
                item_key=item_key,
                entity_id=entity_id,
            )
        return render(request, "home.html", model=model)

    @app.get("/coverage")
    def coverage_page(request: Request):
        with open_readonly(app.state.db_path) as connection:
            model = service.coverage(connection)
        return render(request, "coverage.html", model=model)

    @app.get("/entities")
    def entities_page(request: Request):
        with open_readonly(app.state.db_path) as connection:
            model = service.bank_directory(connection)
        return render(request, "entities.html", model=model)

    @app.get("/indicators")
    def indicators_page(request: Request):
        with open_readonly(app.state.db_path) as connection:
            model = service.indicators(connection)
        return render(request, "indicators.html", model=model)

    @app.get("/api/charts")
    def charts_api(
        bucket: str | None = None,
        statement_date: str | None = None,
        item_key: str | None = None,
        entity_id: str | None = None,
    ) -> dict[str, Any]:
        with open_readonly(app.state.db_path) as connection:
            return service.chart_home(
                connection,
                bucket_id=bucket,
                statement_date=statement_date,
                item_key=item_key,
                entity_id=entity_id,
            )

    @app.get("/api/entities")
    def entities_api() -> dict[str, Any]:
        with open_readonly(app.state.db_path) as connection:
            return service.bank_directory(connection)

    @app.get("/api/indicators")
    def indicators_api() -> dict[str, Any]:
        with open_readonly(app.state.db_path) as connection:
            return service.indicators(connection)

    @app.get("/entities/{entity_id}")
    def entity_page(
        request: Request,
        entity_id: str,
        date: str | None = None,
    ):
        with open_readonly(app.state.db_path) as connection:
            model = service.entity_detail(connection, entity_id, date)
        if model is None:
            raise HTTPException(status_code=404, detail="找不到已採納名單內的機構")
        return render(request, "entity.html", model=model)

    @app.get("/statements/{statement_id}")
    def statement_page(request: Request, statement_id: str):
        with open_readonly(app.state.db_path) as connection:
            model = service.statement_detail(connection, statement_id)
        if model is None:
            raise HTTPException(status_code=404, detail="找不到已採納名單內的報表")
        return render(request, "statement.html", model=model)

    @app.get("/compare")
    def compare_page(
        request: Request,
        bucket: str = "legal",
        statement_date: str = COMPARISON_DATES[1],
        item_key: str | None = None,
        display: str = "amount",
    ):
        with open_readonly(app.state.db_path) as connection:
            model = service.compare(
                connection,
                bucket_id=bucket,
                statement_date=statement_date,
                item_key=item_key,
                display=display,
            )
        return render(request, "compare.html", model=model)

    @app.get("/api/coverage")
    def coverage_api() -> dict[str, Any]:
        with open_readonly(app.state.db_path) as connection:
            return service.coverage(connection)

    @app.get("/api/entities/{entity_id}")
    def entity_api(entity_id: str, date: str | None = None) -> dict[str, Any]:
        with open_readonly(app.state.db_path) as connection:
            model = service.entity_detail(connection, entity_id, date)
        if model is None:
            raise HTTPException(status_code=404, detail="找不到已採納名單內的機構")
        return model

    @app.get("/api/statements/{statement_id}")
    def statement_api(statement_id: str) -> dict[str, Any]:
        with open_readonly(app.state.db_path) as connection:
            model = service.statement_detail(connection, statement_id)
        if model is None:
            raise HTTPException(status_code=404, detail="找不到已採納名單內的報表")
        return model

    @app.get("/api/compare")
    def compare_api(
        bucket: str = Query(...),
        statement_date: str = Query(...),
        item_key: str | None = None,
        display: str | None = None,
    ) -> dict[str, Any]:
        with open_readonly(app.state.db_path) as connection:
            return service.compare(
                connection,
                bucket_id=bucket,
                statement_date=statement_date,
                item_key=item_key,
                display=display,
            )

    @app.get("/api/aggregate")
    def aggregate_api() -> dict[str, Any]:
        return aggregate_placeholder()

    return app
