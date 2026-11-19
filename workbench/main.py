"""FastAPI Workbench scaffold for the Macau banking v3 database."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from workbench import service
from workbench.permissions import (
    DEFAULT_ROLE, ROLES, can_adopt, can_map, can_publish, can_review_statement, can_verify,
)


class RowVerificationPayload(BaseModel):
    label: str | None = None
    debit: str | None = None
    credit: str | None = None
    debit_state: str = "blank"
    credit_state: str = "blank"
    note: str = ""


class StatementReviewPayload(BaseModel):
    note: str = ""
    review_status: str = "reviewed"


class StandardItemPayload(BaseModel):
    standard_item_id: str
    note: str = ""


class RowInsertPayload(BaseModel):
    after_row_id: str | None = None
    at_start: bool = False
    pdf_page: int | None = None
    row_type: str = "item"
    label: str
    debit: str | None = None
    credit: str | None = None
    debit_state: str = "blank"
    credit_state: str = "blank"
    standard_item_id: str | None = None
    note: str = ""


class RowAdjustmentPayload(BaseModel):
    note: str = ""

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).resolve().parent / "templates"
STATIC = Path(__file__).resolve().parent / "static"

app = FastAPI(title="Macau Banking Data Workbench", version="0.1.0")
app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES))


def role_from(request: Request, header_role: str | None = None) -> str:
    candidate = (
        header_role
        or request.headers.get("X-Workbench-Role")
        or request.query_params.get("role")
        or request.cookies.get("workbench_role")
        or DEFAULT_ROLE
    ).strip()
    return candidate if candidate in ROLES else DEFAULT_ROLE


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
        "can_review_statement": can_review_statement(role),
        "can_map": can_map(role),
        **extra,
    }


@app.get("/set-role/{role}")
def set_role(role: str, next: str = "/") -> RedirectResponse:
    if role not in ROLES:
        raise HTTPException(status_code=400, detail="unknown role")
    target = next if next.startswith("/") and not next.startswith("//") else "/"
    response = RedirectResponse(url=target, status_code=303)
    response.set_cookie("workbench_role", role, path="/", httponly=False, samesite="lax")
    return response


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


@app.post("/api/statements/{statement_id}/return-to-review")
def api_return_to_review(
    statement_id: str,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_adopt(role):
        raise HTTPException(status_code=403, detail="adopt capability is required")
    try:
        return service.return_to_review(
            statement_id, actor_from(request), note="Returned to reviewed"
        )
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


@app.get("/api/standard-items")
def api_standard_items() -> list[dict[str, Any]]:
    return service.standard_items()


@app.post("/api/review/{statement_id}/rows/{statement_row_id}/map")
def api_map_row(
    statement_id: str,
    statement_row_id: str,
    payload: StandardItemPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_map(role):
        raise HTTPException(status_code=403, detail="map capability is required")
    try:
        return service.approve_mapping(
            statement_id,
            statement_row_id,
            payload.standard_item_id,
            actor_from(request),
            payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.StatementReviewedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.StatementAdoptedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/review/{statement_id}/adjustments")
def api_statement_adjustments(statement_id: str) -> list[dict[str, Any]]:
    try:
        return service.statement_adjustments(statement_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/review/{statement_id}/rows")
def api_insert_row(
    statement_id: str,
    payload: RowInsertPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_verify(role):
        raise HTTPException(status_code=403, detail="verify capability is required")
    try:
        return service.insert_row(
            statement_id,
            actor=actor_from(request),
            after_row_id=payload.after_row_id,
            at_start=payload.at_start,
            pdf_page=payload.pdf_page,
            row_type=payload.row_type,
            label=payload.label,
            debit=payload.debit,
            credit=payload.credit,
            debit_state=payload.debit_state,
            credit_state=payload.credit_state,
            standard_item_id=payload.standard_item_id,
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.StatementReviewedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.StatementAdoptedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/review/{statement_id}/rows/{statement_row_id}/exclude")
def api_exclude_row(
    statement_id: str,
    statement_row_id: str,
    payload: RowAdjustmentPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_verify(role):
        raise HTTPException(status_code=403, detail="verify capability is required")
    try:
        return service.exclude_row(
            statement_id,
            statement_row_id,
            actor=actor_from(request),
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.StatementReviewedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.StatementAdoptedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/review/{statement_id}/adjustments/{adjustment_id}/restore")
def api_restore_adjustment(
    statement_id: str,
    adjustment_id: str,
    payload: RowAdjustmentPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_verify(role):
        raise HTTPException(status_code=403, detail="verify capability is required")
    try:
        return service.restore_adjustment(
            statement_id,
            adjustment_id,
            actor=actor_from(request),
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except service.StatementReviewedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.StatementAdoptedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    except service.StatementReviewedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except service.StatementAdoptedError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/review/{statement_id}/review")
def api_review_statement(
    statement_id: str,
    payload: StatementReviewPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_review_statement(role):
        raise HTTPException(status_code=403, detail="review capability is required")
    try:
        return service.mark_statement_reviewed(
            statement_id,
            actor_from(request),
            note=payload.note,
            review_status=payload.review_status,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request) -> HTMLResponse:
    queue = service.review_queue()
    return templates.TemplateResponse(
        request,
        "review_queue.html",
        template_context(
            request,
            queue=queue,
            pending_review=[item for item in queue if item["review_status"] != "reviewed"],
            reviewed_held=[item for item in queue if item["review_status"] == "reviewed"],
            adopted=service.adopted_statements(),
        ),
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
    adoption_row = next(
        (item for item in service.adopted_statements() if item["statement_id"] == statement_id),
        None,
    )
    return templates.TemplateResponse(
        request,
        "review_detail.html",
        template_context(
            request,
            statement=detail,
            queue_row=queue_row,
            adoption_row=adoption_row,
            rows=service.review_rows(statement_id),
            adjustments=service.statement_adjustments(statement_id),
            standard_items=service.standard_items(),
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
    metrics = {
        item["statement_id"]: item
        for item in (service.review_queue() + service.adopted_statements())
    }
    return templates.TemplateResponse(
        request,
        "statements.html",
        template_context(
            request,
            summary=service.summary(),
            statements=service.list_statements(status=status),
            metrics=metrics,
            status=status,
        ),
    )


@app.get("/statements/{statement_id}", response_class=HTMLResponse)
def statement_page(request: Request, statement_id: str) -> HTMLResponse:
    detail = service.statement_detail(statement_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="statement not found")
    adoption_row = next(
        (item for item in service.adopted_statements() if item["statement_id"] == statement_id),
        None,
    )
    return templates.TemplateResponse(
        request,
        "statement_detail.html",
        template_context(
            request,
            statement=detail,
            rows=service.statement_rows(statement_id),
            adoption_row=adoption_row,
        ),
    )
