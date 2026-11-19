"""FastAPI Workbench scaffold for the Macau banking v3 database."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from workbench import service
from workbench.permissions import DEFAULT_ROLE, can_adopt, can_publish, can_verify


class RowVerificationPayload(BaseModel):
    label: str | None = None
    debit: str | None = None
    credit: str | None = None
    debit_state: str = "blank"
    credit_state: str = "blank"
    note: str = ""

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).resolve().parent / "templates"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Macau Banking Data Workbench", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES))


def role_from(request: Request, header_role: str | None = None) -> str:
    return (header_role or request.headers.get("X-Workbench-Role") or DEFAULT_ROLE).strip()


def actor_from(request: Request) -> str:
    return (request.headers.get("X-Workbench-Actor") or "workbench").strip()


def template_context(request: Request, **extra: Any) -> dict[str, Any]:
    role = role_from(request)
    return {
        "request": request,
        "role": role,
        "can_verify": can_verify(role),
        "can_adopt": can_adopt(role),
        "can_publish": can_publish(role),
        **extra,
    }


@app.get("/health")
def health() -> dict[str, Any]:
    return {"status": "ok", **service.summary()}


@app.get("/api/summary")
def api_summary() -> dict[str, Any]:
    return service.summary()


@app.get("/api/statements")
def api_statements(
    status: str = Query("all", pattern="^(all|active|held)$"),
    limit: int = Query(200, ge=1, le=1000),
) -> list[dict[str, Any]]:
    return service.list_statements(status=status, limit=limit)


@app.get("/api/statements/{statement_id}")
def api_statement(statement_id: str) -> dict[str, Any]:
    detail = service.statement_detail(statement_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="statement not found")
    return detail


@app.get("/api/statements/{statement_id}/rows")
def api_rows(
    statement_id: str,
    limit: int = Query(1000, ge=1, le=5000),
) -> list[dict[str, Any]]:
    return service.statement_rows(statement_id, limit=limit)


@app.post("/api/statements/{statement_id}/adopt")
def api_adopt(
    statement_id: str,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_adopt(role):
        raise HTTPException(status_code=403, detail="adopt capability is required")
    try:
        return service.adopt_statement(statement_id, actor_from(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/statements/{statement_id}/publish")
def api_publish(
    statement_id: str,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> JSONResponse:
    role = role_from(request, x_workbench_role)
    if not can_publish(role):
        raise HTTPException(status_code=403, detail="publish capability is required")
    try:
        return JSONResponse(service.publish_statement(statement_id, actor_from(request)))
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@app.get("/api/review/queue")
def api_review_queue() -> list[dict[str, Any]]:
    return service.review_queue()


@app.get("/api/review/{statement_id}/rows")
def api_review_rows(
    statement_id: str,
    limit: int = Query(1000, ge=1, le=5000),
) -> list[dict[str, Any]]:
    return service.review_rows(statement_id, limit=limit)


@app.post("/api/review/{statement_id}/rows/{statement_row_id}/verify")
def api_verify_row(
    statement_id: str,
    statement_row_id: str,
    payload: RowVerificationPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_verify(role):
        raise HTTPException(status_code=403, detail="verify capability is required")
    try:
        return service.verify_row(
            statement_id,
            statement_row_id,
            label=payload.label,
            debit=payload.debit,
            credit=payload.credit,
            debit_state=payload.debit_state,
            credit_state=payload.credit_state,
            note=payload.note,
            actor=actor_from(request),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "review_queue.html",
        template_context(request, queue=service.review_queue()),
    )


@app.get("/review/{statement_id}", response_class=HTMLResponse)
def review_detail_page(request: Request, statement_id: str) -> HTMLResponse:
    detail = service.statement_detail(statement_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="statement not found")
    queue_row = next(
        (item for item in service.review_queue() if item["statement_id"] == statement_id),
        None,
    )
    return templates.TemplateResponse(
        request,
        "review_detail.html",
        template_context(
            request,
            statement=detail,
            queue_row=queue_row,
            rows=service.review_rows(statement_id),
        ),
    )


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        template_context(request, summary=service.summary()),
    )


@app.get("/statements", response_class=HTMLResponse)
def statements_page(
    request: Request,
    status: str = Query("all", pattern="^(all|active|held)$"),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "statements.html",
        template_context(
            request,
            summary=service.summary(),
            statements=service.list_statements(status=status),
            status=status,
        ),
    )


@app.get("/statements/{statement_id}", response_class=HTMLResponse)
def statement_page(request: Request, statement_id: str) -> HTMLResponse:
    detail = service.statement_detail(statement_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="statement not found")
    return templates.TemplateResponse(
        request,
        "statement_detail.html",
        template_context(
            request,
            statement=detail,
            rows=service.statement_rows(statement_id),
        ),
    )
