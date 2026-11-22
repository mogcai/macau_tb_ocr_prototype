"""Background import jobs for the Workbench.

The Workbench never runs OCR inside an HTTP request.  It stores a job row,
starts ``tools/import_issue.py --target v3`` in a background subprocess and
polls the job record.  Import jobs are operational control records: the
financial evidence they produce lives in the V3 extraction tables.

Safety rules:

- only ``--target v3`` is ever launched; ``--publish`` is never passed;
- every job keeps its argv, log and manifest path;
- a queued/running duplicate of the same source is rejected;
- stale jobs from a previous process are marked ``interrupted`` at startup,
  never silently re-run.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from workbench.config import ROOT
from workbench.db import connect_v3

SCHEMA_FILE = ROOT / "database" / "v3" / "016_import_jobs.sql"
IMPORTER = ROOT / "tools" / "import_issue.py"
IMPORT_LOG_DIR = ROOT / "outputs" / "import_jobs"
UPLOAD_DIR = ROOT / "input" / ".uploads"
MAX_PDF_BYTES = 250 * 1024 * 1024

TERMINAL_STATUSES = {"completed", "failed", "needs_decision", "interrupted", "cancelled"}
ACTIVE_STATUSES = {"queued", "running"}

RESULT_STATUS_MAP = {
    "completed": "completed",
    "planned": "completed",
    "already_imported": "completed",
    "failed": "failed",
    "usage_error": "failed",
    "import_locked": "failed",
    "new_source_version_required": "needs_decision",
    "already_imported_content_conflict": "needs_decision",
}

_issue_key_re = re.compile(r"^\d{4}-\d{2}$")
_initialised: set[str] = set()


class ImportJobError(ValueError):
    """Invalid import job request."""

    status_code = 400


class ImportJobNotFound(LookupError):
    status_code = 404


class ImportJobConflict(ValueError):
    status_code = 409


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(db_path: Path | None = None) -> None:
    key = str(_target_db_path(db_path))
    if key in _initialised:
        return
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    conn = connect_v3(db_path, write=True)
    try:
        conn.executescript(sql)
        conn.commit()
    finally:
        conn.close()
    _initialised.add(key)


def _target_db_path(db_path: Path | None = None) -> Path:
    if db_path is not None:
        return Path(db_path).resolve()
    from workbench import db as workbench_db

    return Path(workbench_db.V3_DB).resolve()


def _conn(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_schema(db_path)
    return connect_v3(db_path, write=True)


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    job = dict(row)
    job["argv"] = json.loads(job.pop("argv_json") or "[]")
    job["dry_run"] = "--dry-run" in job["argv"]
    result_json = job.pop("result_json", None)
    job["result"] = json.loads(result_json) if result_json else None
    job["is_active"] = job["status"] in ACTIVE_STATUSES
    return job


def list_jobs(limit: int = 50) -> list[dict[str, Any]]:
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM import_jobs ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_job(row) for row in rows]
    finally:
        conn.close()


def get_job(job_id: str) -> dict[str, Any]:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM import_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise ImportJobNotFound(f"import job not found: {job_id}")
    return _row_to_job(row)


def recover_stale_jobs() -> int:
    """Mark queued/running jobs from a previous process as interrupted."""
    if not _target_db_path().is_file():
        return 0
    conn = _conn()
    try:
        cursor = conn.execute(
            "UPDATE import_jobs SET status='interrupted', updated_at=?, "
            "error_type='Interrupted', "
            "error_message='Workbench restarted while this job was queued or running', "
            "finished_at=COALESCE(finished_at, ?) "
            "WHERE status IN ('queued', 'running')",
            (now(), now()),
        )
        conn.commit()
        return cursor.rowcount
    finally:
        conn.close()


def _validate_url(source: str) -> None:
    _validate_url_policy(source)


def _validate_url_policy(source: str) -> None:
    from tools import v3_ingest

    try:
        v3_ingest.validate_v3_url(source)
    except v3_ingest.V3IngestError as exc:
        raise ImportJobError(str(exc)) from exc


def _derived_issue_key(source: str) -> str | None:
    from tools import v3_ingest

    return v3_ingest.derive_issue_key(source)


def _normalise_issue_key(issue_key: str | None, source: str) -> str | None:
    derived = _derived_issue_key(source)
    if issue_key is None or not str(issue_key).strip():
        return derived
    key = str(issue_key).strip()
    if not _issue_key_re.fullmatch(key):
        raise ImportJobError("issue key must look like YYYY-NN")
    if derived and derived != key:
        raise ImportJobError(
            f"issue key {key} disagrees with the source filename ({derived})"
        )
    return key


def _base_argv(source: str) -> list[str]:
    return [
        sys.executable,
        str(IMPORTER),
        source,
        "--target",
        "v3",
        "--json",
    ]


def _insert_job(
    *,
    operation: str,
    source_kind: str,
    source: str,
    issue_key: str | None,
    argv: list[str],
    created_by: str,
    retry_of: str | None = None,
) -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    stamp = now()
    log_path = IMPORT_LOG_DIR / f"{job_id}.log"
    conn = _conn()
    try:
        active = conn.execute(
            "SELECT job_id FROM import_jobs WHERE source = ? "
            "AND status IN ('queued', 'running') LIMIT 1",
            (source,),
        ).fetchone()
        if active is not None:
            raise ImportJobConflict(
                f"an import for this source is already queued or running "
                f"({active['job_id']})"
            )
        conn.execute(
            "INSERT INTO import_jobs (job_id, created_at, updated_at, created_by, "
            "status, operation, source_kind, source, issue_key, argv_json, "
            "log_path, retry_of) VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
            (
                job_id, stamp, stamp, created_by, operation, source_kind,
                source, issue_key, json.dumps(argv), str(log_path), retry_of,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    thread = threading.Thread(
        target=lambda: _execute_job(job_id),
        name=f"import-job-{job_id[:8]}",
        daemon=True,
    )
    thread.start()
    return get_job(job_id)


def create_url_job(
    source: str,
    *,
    issue_key: str | None = None,
    dry_run: bool = False,
    created_by: str = "workbench",
) -> dict[str, Any]:
    source = str(source or "").strip()
    if not source:
        raise ImportJobError("source URL is required")
    if urlparse(source).scheme != "https":
        raise ImportJobError("V3 import source must be an HTTPS URL")
    _validate_url(source)
    key = _normalise_issue_key(issue_key, source)
    argv = _base_argv(source)
    if key:
        argv += ["--issue-key", key]
    if dry_run:
        argv.append("--dry-run")
    return _insert_job(
        operation="dry_run" if dry_run else "full_import",
        source_kind="url",
        source=source,
        issue_key=key,
        argv=argv,
        created_by=created_by,
    )


def _safe_pdf_name(filename: str) -> str:
    name = Path(str(filename or "")).name.strip()
    if not name or not name.lower().endswith(".pdf"):
        raise ImportJobError("uploaded file must have a .pdf filename")
    if any(ord(char) < 32 for char in name):
        raise ImportJobError("uploaded filename contains control characters")
    return name


def store_upload(filename: str, data: bytes) -> Path:
    name = _safe_pdf_name(filename)
    if not data:
        raise ImportJobError("uploaded PDF is empty")
    if len(data) > MAX_PDF_BYTES:
        raise ImportJobError("uploaded PDF exceeds 250 MB")
    if not data.startswith(b"%PDF-"):
        raise ImportJobError("uploaded file does not start with %PDF-")
    target_dir = UPLOAD_DIR / uuid.uuid4().hex
    target_dir.mkdir(parents=True, exist_ok=False)
    path = target_dir / name
    path.write_bytes(data)
    return path


def create_upload_job(
    path: Path,
    *,
    issue_key: str | None = None,
    created_by: str = "workbench",
) -> dict[str, Any]:
    path = Path(path).expanduser().resolve()
    if not path.is_file() or path.is_symlink():
        raise ImportJobError(f"uploaded PDF not found: {path}")
    key = _normalise_issue_key(issue_key, str(path))
    argv = _base_argv(str(path)) + ["--allow-local-source"]
    if key:
        argv += ["--issue-key", key]
    return _insert_job(
        operation="full_import",
        source_kind="upload",
        source=str(path),
        issue_key=key,
        argv=argv,
        created_by=created_by,
    )


def retry_job(job_id: str, *, created_by: str = "workbench") -> dict[str, Any]:
    job = get_job(job_id)
    if job["status"] not in ("failed", "interrupted"):
        raise ImportJobConflict(
            f"job is {job['status']}; only failed or interrupted jobs can be retried"
        )
    if job["source_kind"] == "upload" and not Path(job["source"]).is_file():
        raise ImportJobError("the uploaded source PDF is no longer available")
    return _insert_job(
        operation=job["operation"],
        source_kind=job["source_kind"],
        source=job["source"],
        issue_key=job["issue_key"],
        argv=list(job["argv"]),
        created_by=created_by,
        retry_of=job_id,
    )


def decision_job(
    job_id: str,
    *,
    decision: str,
    reason: str,
    created_by: str = "workbench",
) -> dict[str, Any]:
    if decision not in ("reprocess", "new_version"):
        raise ImportJobError("decision must be reprocess or new_version")
    reason = str(reason or "").strip()
    if not reason:
        raise ImportJobError("a reason is required for reprocess or new_version")
    job = get_job(job_id)
    if job["status"] not in TERMINAL_STATUSES:
        raise ImportJobConflict("wait for the current job to finish first")
    if job["source_kind"] == "upload" and not Path(job["source"]).is_file():
        raise ImportJobError("the uploaded source PDF is no longer available")
    cleaned: list[str] = []
    skip = False
    for arg in job["argv"]:
        if skip:
            skip = False
            continue
        if arg == "--reason":
            skip = True
            continue
        if arg in ("--reprocess", "--new-version", "--dry-run"):
            continue
        cleaned.append(arg)
    argv = cleaned + [f"--{decision}", "--reason", reason]
    return _insert_job(
        operation="full_import",
        source_kind=job["source_kind"],
        source=job["source"],
        issue_key=job["issue_key"],
        argv=argv,
        created_by=created_by,
        retry_of=job_id,
    )


def _update(job_id: str, **fields: Any) -> None:
    fields["updated_at"] = now()
    assignments = ", ".join(f"{key} = ?" for key in fields)
    conn = _conn()
    try:
        conn.execute(
            f"UPDATE import_jobs SET {assignments} WHERE job_id = ?",
            (*fields.values(), job_id),
        )
        conn.commit()
    finally:
        conn.close()


def _parse_result(stdout: str) -> dict[str, Any] | None:
    for line in reversed([item for item in stdout.splitlines() if item.strip()]):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _apply_result(job_id: str, *, code: int, stdout: str, stderr: str) -> None:
    result = _parse_result(stdout)
    stamp = now()
    if result is None:
        message = (stderr.strip() or stdout.strip() or f"importer exited {code}")[-2000:]
        _update(
            job_id,
            status="failed",
            finished_at=stamp,
            error_type="ImporterError",
            error_message=message,
        )
        return
    result_status = str(result.get("status") or "failed")
    manifest_path = result.get("manifest_path")
    manifest = _read_manifest(manifest_path)
    status = RESULT_STATUS_MAP.get(result_status, "failed")
    error = result.get("error") or {}
    _update(
        job_id,
        status=status,
        result_status=result_status,
        result_json=json.dumps(result, ensure_ascii=False),
        issue_key=result.get("issue_key") or None,
        run_dir=result.get("run_dir") or None,
        manifest_path=str(manifest_path) if manifest_path else None,
        pdf_path=str(manifest.get("pdf_path")) if manifest.get("pdf_path") else None,
        pdf_sha256=result.get("pdf_sha256") or manifest.get("pdf_sha256"),
        error_type=error.get("type"),
        error_message=error.get("message"),
        finished_at=stamp,
    )


def _read_manifest(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _execute_job(job_id: str) -> None:
    job = get_job(job_id)
    argv = list(job["argv"])
    log_path = Path(job["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _update(job_id, status="running", started_at=now())
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"$ {' '.join(argv)}\n")
        log.flush()
        try:
            completed = subprocess.run(
                argv, cwd=ROOT, text=True, capture_output=True, check=False
            )
        except OSError as exc:
            log.write(f"\n[launch error] {exc}\n")
            _update(
                job_id,
                status="failed",
                finished_at=now(),
                error_type=type(exc).__name__,
                error_message=str(exc),
            )
            return
        log.write(completed.stdout)
        log.write(completed.stderr)
        log.write(f"\n[exit {completed.returncode}]\n")
    _apply_result(
        job_id,
        code=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )


def _log_tail(path: str | Path | None, *, max_bytes: int = 20000) -> str:
    if not path:
        return ""
    try:
        data = Path(path).read_bytes()
    except OSError:
        return ""
    if len(data) > max_bytes:
        data = b"[... truncated ...]\n" + data[-max_bytes:]
    return data.decode("utf-8", errors="replace")


def _steps(job: dict[str, Any]) -> list[dict[str, str]]:
    if job.get("operation") == "dry_run":
        preflight = {"queued": "pending", "running": "running"}.get(
            job["status"], job["status"]
        )
        return [{"name": "preflight", "status": preflight}]
    manifest = _read_manifest(job.get("manifest_path"))
    result = job.get("result") or {}
    steps = [
        {
            "name": "download",
            "status": manifest.get("download_status") or ("completed" if result else "pending"),
        },
        {
            "name": "ocr",
            "status": manifest.get("ocr_status") or ("pending" if job["status"] in ACTIVE_STATUSES else "unknown"),
        },
        {
            "name": "v3_replay",
            "status": manifest.get("v3_replay_status") or ("pending" if job["status"] in ACTIVE_STATUSES else "unknown"),
        },
    ]
    return steps


def job_detail(job_id: str) -> dict[str, Any]:
    from workbench.ocr_quality import summarize_run_dir

    job = get_job(job_id)
    manifest = _read_manifest(job.get("manifest_path"))
    run_dir = job.get("run_dir") or (manifest or {}).get("run_dir")
    ocr_quality = summarize_run_dir(run_dir) if run_dir else None
    source_document_id = None
    if isinstance(manifest, dict):
        source_document_id = (manifest.get("v3") or {}).get("source_document_id")
    return {
        "job": job,
        "steps": _steps(job),
        "manifest": manifest or None,
        "log_tail": _log_tail(job.get("log_path")),
        "ocr_quality": ocr_quality,
        "source_document_id": source_document_id,
    }


def log_text(job_id: str) -> str:
    job = get_job(job_id)
    return _log_tail(job.get("log_path"))
