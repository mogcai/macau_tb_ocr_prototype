"""FastAPI Workbench scaffold for the Macau banking v3 database."""
from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from workbench import import_jobs, intake, service
from workbench.permissions import (
    DEFAULT_ROLE, ROLES, can_adopt, can_catalogue, can_classify, can_import,
    can_map, can_publish, can_review_statement, can_verify,
)


class RowVerificationPayload(BaseModel):
    label: str | None = None
    debit: str | None = None
    credit: str | None = None
    debit_state: str = "blank"
    credit_state: str = "blank"
    verified_fields: list[str] | None = None
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


class StandardItemCreatePayload(BaseModel):
    canonical_zh: str | None = None
    canonical_pt: str | None = None
    canonical_en: str | None = None
    item_key: str | None = None
    parent_standard_item_id: str | None = None
    category: str | None = None
    note: str = ""


class StandardItemLifecyclePayload(BaseModel):
    note: str = ""


class ImportJobPayload(BaseModel):
    source: str
    issue_key: str | None = None
    dry_run: bool = False


class ImportDecisionPayload(BaseModel):
    decision: str = "reprocess"
    reason: str


class PageDecisionPayload(BaseModel):
    page_role: str
    decision: str
    entity_id: str | None = None
    statement_date: str | None = None
    statement_type: str = "balance_sheet"
    note: str = ""


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = Path(__file__).resolve().parent / "templates"
STATIC = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(_: FastAPI):
    import_jobs.recover_stale_jobs()
    yield


app = FastAPI(
    title="Macau Banking Data Workbench", version="0.1.0", lifespan=lifespan
)
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
        "can_catalogue": can_catalogue(role),
        "can_import": can_import(role),
        "can_classify": can_classify(role),
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
def api_standard_items(include_inactive: bool = False) -> list[dict[str, Any]]:
    return service.standard_items(include_inactive=include_inactive)


@app.get("/api/catalogue/items")
def api_catalogue_items() -> list[dict[str, Any]]:
    return service.standard_item_admin_rows()


@app.post("/api/catalogue/items")
def api_catalogue_create(
    payload: StandardItemCreatePayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_catalogue(role):
        raise HTTPException(status_code=403, detail="catalogue capability is required")
    try:
        return service.create_standard_item(
            actor=actor_from(request),
            canonical_zh=payload.canonical_zh,
            canonical_pt=payload.canonical_pt,
            canonical_en=payload.canonical_en,
            item_key=payload.item_key,
            parent_standard_item_id=payload.parent_standard_item_id,
            category=payload.category,
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/catalogue/items/{standard_item_id}/approve")
def api_catalogue_approve(
    standard_item_id: str,
    payload: StandardItemLifecyclePayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_catalogue(role):
        raise HTTPException(status_code=403, detail="catalogue capability is required")
    try:
        return service.approve_standard_item(
            standard_item_id,
            actor=actor_from(request),
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/catalogue/items/{standard_item_id}/retire")
def api_catalogue_retire(
    standard_item_id: str,
    payload: StandardItemLifecyclePayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_catalogue(role):
        raise HTTPException(status_code=403, detail="catalogue capability is required")
    try:
        return service.retire_standard_item(
            standard_item_id,
            actor=actor_from(request),
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/catalogue/items/{standard_item_id}/reopen")
def api_catalogue_reopen(
    standard_item_id: str,
    payload: StandardItemLifecyclePayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_catalogue(role):
        raise HTTPException(status_code=403, detail="catalogue capability is required")
    try:
        return service.reopen_standard_item(
            standard_item_id,
            actor=actor_from(request),
            note=payload.note,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


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
    except service.InactiveStandardItemError as exc:
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
    except service.InactiveStandardItemError as exc:
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
            verified_fields=payload.verified_fields,
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


@app.get("/api/imports")
def api_imports(limit: int = Query(50, ge=1, le=200)) -> list[dict[str, Any]]:
    return import_jobs.list_jobs(limit=limit)


@app.post("/api/imports", status_code=202)
def api_import_create(
    payload: ImportJobPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> JSONResponse:
    role = role_from(request, x_workbench_role)
    if not can_import(role):
        raise HTTPException(status_code=403, detail="import capability is required")
    try:
        job = import_jobs.create_url_job(
            payload.source,
            issue_key=payload.issue_key,
            dry_run=payload.dry_run,
            created_by=actor_from(request),
        )
    except import_jobs.ImportJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except import_jobs.ImportJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(job, status_code=202)


@app.post("/api/imports/upload", status_code=202)
async def api_import_upload(
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> JSONResponse:
    role = role_from(request, x_workbench_role)
    if not can_import(role):
        raise HTTPException(status_code=403, detail="import capability is required")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > import_jobs.MAX_PDF_BYTES:
        raise HTTPException(status_code=413, detail="uploaded PDF exceeds 250 MB")
    data = await request.body()
    staging: Path | None = None
    try:
        staging = import_jobs.store_upload(request.headers.get("x-filename", ""), data)
        job = import_jobs.create_upload_job(
            staging,
            issue_key=request.headers.get("x-issue-key"),
            created_by=actor_from(request),
        )
    except import_jobs.ImportJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        if staging is not None:
            shutil.rmtree(staging.parent, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(job, status_code=202)


@app.get("/api/imports/{job_id}")
def api_import_detail(job_id: str) -> dict[str, Any]:
    try:
        return import_jobs.job_detail(job_id)
    except import_jobs.ImportJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/api/imports/{job_id}/log", response_class=PlainTextResponse)
def api_import_log(job_id: str) -> PlainTextResponse:
    try:
        return PlainTextResponse(import_jobs.log_text(job_id))
    except import_jobs.ImportJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/imports/{job_id}/retry", status_code=202)
def api_import_retry(
    job_id: str,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> JSONResponse:
    role = role_from(request, x_workbench_role)
    if not can_import(role):
        raise HTTPException(status_code=403, detail="import capability is required")
    try:
        job = import_jobs.retry_job(job_id, created_by=actor_from(request))
    except import_jobs.ImportJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except import_jobs.ImportJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(job, status_code=202)


@app.post("/api/imports/{job_id}/decision", status_code=202)
def api_import_decision(
    job_id: str,
    payload: ImportDecisionPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> JSONResponse:
    role = role_from(request, x_workbench_role)
    if not can_import(role):
        raise HTTPException(status_code=403, detail="import capability is required")
    try:
        job = import_jobs.decision_job(
            job_id,
            decision=payload.decision,
            reason=payload.reason,
            created_by=actor_from(request),
        )
    except import_jobs.ImportJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except import_jobs.ImportJobConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse(job, status_code=202)


@app.get("/imports", response_class=HTMLResponse)
def imports_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "imports.html",
        template_context(request, jobs=import_jobs.list_jobs(limit=100)),
    )


@app.get("/imports/{job_id}", response_class=HTMLResponse)
def import_detail_page(request: Request, job_id: str) -> HTMLResponse:
    try:
        detail = import_jobs.job_detail(job_id)
    except import_jobs.ImportJobNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "import_detail.html",
        template_context(request, **detail),
    )


@app.get("/api/intake")
def api_intake() -> list[dict[str, Any]]:
    return intake.list_batches()


@app.get("/api/intake/{source_document_id}")
def api_intake_batch(source_document_id: str) -> dict[str, Any]:
    try:
        return intake.batch_detail(source_document_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/intake/{source_document_id}/pages/{document_page_id}")
def api_intake_decide(
    source_document_id: str,
    document_page_id: str,
    payload: PageDecisionPayload,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_classify(role):
        raise HTTPException(status_code=403, detail="classify capability is required")
    try:
        return intake.save_page_decision(
            source_document_id,
            document_page_id,
            page_role=payload.page_role,
            decision=payload.decision,
            entity_id=payload.entity_id,
            statement_date=payload.statement_date,
            statement_type=payload.statement_type,
            note=payload.note,
            actor=actor_from(request),
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except intake.IntakeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/intake/{source_document_id}/build")
def api_intake_build(
    source_document_id: str,
    request: Request,
    x_workbench_role: str | None = Header(default=None),
) -> dict[str, Any]:
    role = role_from(request, x_workbench_role)
    if not can_classify(role):
        raise HTTPException(status_code=403, detail="classify capability is required")
    try:
        return intake.build_statements(source_document_id, actor_from(request))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except intake.IntakeConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/intake", response_class=HTMLResponse)
def intake_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "intake.html",
        template_context(request, batches=intake.list_batches()),
    )


@app.get("/intake/{source_document_id}", response_class=HTMLResponse)
def intake_batch_page(request: Request, source_document_id: str) -> HTMLResponse:
    try:
        detail = intake.batch_detail(source_document_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        request,
        "intake_batch.html",
        template_context(request, **detail),
    )


@app.get("/catalogue", response_class=HTMLResponse)
def catalogue_page(
    request: Request,
    status: str = Query("all", pattern="^(all|draft|approved|merged|retired)$"),
    q: str = "",
) -> HTMLResponse:
    rows = service.standard_item_admin_rows()
    counts = {
        "all": len(rows),
        "draft": sum(1 for row in rows if row["item_status"] == "draft"),
        "approved": sum(1 for row in rows if row["item_status"] == "approved"),
        "merged": sum(1 for row in rows if row["item_status"] == "merged"),
        "retired": sum(1 for row in rows if row["item_status"] == "retired"),
    }
    if status != "all":
        rows = [row for row in rows if row["item_status"] == status]
    needle = q.strip().casefold()
    if needle:
        rows = [
            row for row in rows
            if needle in " ".join(
                part for part in (
                    row.get("item_key"), row.get("canonical_zh"),
                    row.get("canonical_pt"), row.get("canonical_en"),
                    row.get("path"),
                ) if part
            ).casefold()
        ]
    parents = [
        row for row in service.standard_items(include_inactive=True)
        if row["item_status"] in {"approved", "draft"}
    ]
    return templates.TemplateResponse(
        request,
        "catalogue.html",
        template_context(
            request,
            rows=rows,
            counts=counts,
            status=status,
            q=q,
            parents=parents,
        ),
    )


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
            used_standard_item_ids=service.statement_used_standard_items(statement_id),
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
