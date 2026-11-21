"""V3 raw-ingestion entry point used by ``tools/import_issue.py --target v3``.

This module deliberately stops at raw OCR evidence.  It never creates Statements,
reviews, mappings, adoptions or publications.
"""
from __future__ import annotations

import argparse
import contextlib
import errno
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from tools import import_issue as imp
from tools import replay_runs_to_v3 as replay

ROOT = imp.ROOT
DEFAULT_V3_DB = ROOT / "data" / "macau_balancete_v3.sqlite"
DEFAULT_ALLOWED_HOST = "images.bo.dsaj.gov.mo"
MAX_REDIRECTS = 5
MAX_PDF_BYTES = imp.MAX_PDF_BYTES
REQUIRED_TABLES = {
    "gazette_issues", "source_documents", "document_pages", "extraction_runs",
    "page_extractions", "extracted_rows",
}


class V3IngestError(RuntimeError):
    pass


class ImportLocked(V3IngestError):
    pass


class OperatorDecision(V3IngestError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def now() -> str:
    return imp.now()


def sha256_file(path: Path) -> str:
    return imp.sha256_file(path)


def allowed_hosts() -> set[str]:
    raw = os.environ.get("GAZETTE_ALLOWED_HOSTS", DEFAULT_ALLOWED_HOST)
    return {part.strip().lower() for part in raw.split(",") if part.strip()}


def _is_forbidden_address(address: str) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    return bool(
        ip.is_private or ip.is_loopback or ip.is_link_local or
        ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


def validate_v3_url(url: str) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise V3IngestError("V3 source URL must use HTTPS")
    if parsed.username or parsed.password:
        raise V3IngestError("URL user-info is not allowed")
    if parsed.query or parsed.fragment:
        raise V3IngestError("URL query strings and fragments are not allowed")
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise V3IngestError("URL has no hostname")
    if hostname not in allowed_hosts():
        raise V3IngestError(f"hostname not allowlisted: {hostname}")
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        raise V3IngestError("IP-literal URLs are not allowed")
    try:
        infos = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise V3IngestError(f"cannot resolve {hostname}: {exc}") from exc
    for info in infos:
        if _is_forbidden_address(info[4][0]):
            raise V3IngestError(f"hostname resolves to forbidden address: {info[4][0]}")
    return parsed


class _RedirectPolicy(urllib.request.HTTPRedirectHandler):
    def __init__(self):
        super().__init__()
        self.redirects = 0

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.redirects += 1
        if self.redirects > MAX_REDIRECTS:
            raise V3IngestError("too many redirects")
        validate_v3_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _open_url(url: str):
    opener = urllib.request.build_opener(_RedirectPolicy())
    return opener.open(
        urllib.request.Request(
            url, headers={"User-Agent": "Mozilla/5.0 macau-balancete-ocr/0.7"}
        ),
        timeout=120,
    )


def canonical_issue_key(value: str) -> str:
    return replay.canonical_issue_key(value)


def derive_issue_key(source: str) -> str | None:
    parsed = urllib.parse.urlparse(source)
    name = Path(urllib.parse.unquote(parsed.path)).name if parsed.scheme else Path(source).name
    match = re.fullmatch(r"anotariais-(\d+)-(\d{4})", Path(name).stem, re.I)
    if not match:
        return None
    return f"{match.group(2)}-{int(match.group(1)):02d}"


def resolve_issue_key(args: argparse.Namespace, source: str | None) -> str:
    derived = derive_issue_key(source) if source else None
    explicit = getattr(args, "issue_key", None)
    if explicit:
        key = canonical_issue_key(explicit)
        if derived and derived != key:
            raise V3IngestError(
                f"--issue-key {key} disagrees with source filename {derived}"
            )
        return key
    if not derived:
        raise V3IngestError("issue key cannot be derived; supply --issue-key YYYY-NN")
    return derived


def resolve_v3_db(args: argparse.Namespace) -> Path:
    return (args.v3_db or DEFAULT_V3_DB).expanduser().resolve()


def resolve_v3_outdir(args: argparse.Namespace, issue_key: str, job_id: str) -> Path:
    if args.outdir:
        outdir = args.outdir.expanduser().resolve()
    else:
        year, issue = issue_key.split("-")
        outdir = ROOT / "outputs" / "runs" / f"issue{int(issue)}_{year}__job_{job_id}"
    if not outdir.is_relative_to(ROOT / "outputs" / "runs") and not args.allow_outside_output:
        raise V3IngestError(f"output directory escapes managed root: {outdir}")
    return outdir


def resolve_v3_input(args: argparse.Namespace) -> Path:
    input_dir = (args.input_dir or (ROOT / "input")).expanduser().resolve()
    managed = (ROOT / "input").resolve()
    if input_dir != managed and managed not in input_dir.parents:
        raise V3IngestError(f"input directory is not managed: {input_dir}")
    return input_dir


def validate_v3_db(db_path: Path) -> None:
    if not db_path.is_file():
        raise V3IngestError(f"V3 database not found: {db_path}")
    if "baseline" in db_path.parts:
        raise V3IngestError("refusing to write to a baseline snapshot")
    conn = sqlite3.connect(db_path)
    try:
        names = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        missing = REQUIRED_TABLES - names
        if missing:
            raise V3IngestError(f"not a V3 database; missing tables: {sorted(missing)}")
        conn.execute("PRAGMA foreign_keys = ON")
    finally:
        conn.close()


def job_id_for(args: argparse.Namespace) -> str:
    if args.outdir:
        name = args.outdir.name
        if "__job_" in name:
            candidate = re.sub(r"[^A-Za-z0-9._-]+", "_", name.rsplit("__job_", 1)[1]).strip("._")
            if candidate and candidate not in (".", ".."):
                return candidate
    return uuid.uuid4().hex[:8]


@contextlib.contextmanager
def import_lock(db_path: Path):
    real = Path(os.path.realpath(db_path))
    lock_path = real.with_name(real.name + ".import.lock")
    handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ImportLocked(f"another import holds {lock_path}") from exc
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _copy_local_source(
    source: str,
    input_dir: Path,
    job_id: str,
    explicit_name: str | None = None,
) -> dict[str, Any]:
    path = Path(source).expanduser()
    if not path.is_file() or path.is_symlink():
        raise V3IngestError(f"local source must be a regular file: {source}")
    if path.suffix.lower() != ".pdf":
        raise V3IngestError("local source must be a .pdf file")
    filename = Path(explicit_name).name if explicit_name else path.name
    if not filename.lower().endswith(".pdf"):
        raise V3IngestError("managed source filename must end with .pdf")
    temp = input_dir / f".local-{job_id}-{uuid.uuid4().hex[:8]}.part"
    temp.write_bytes(path.read_bytes())
    return {
        "temp": temp,
        "source_url": "",
        "source_kind": "local_file",
        "source_filename": filename,
        "bytes": temp.stat().st_size,
        "sha256": sha256_file(temp),
    }


def download_source(args: argparse.Namespace, input_dir: Path, job_id: str) -> dict[str, Any]:
    source = args.source
    if source is None:
        raise V3IngestError("source is required")
    parsed = urllib.parse.urlparse(source)
    if parsed.scheme not in ("http", "https"):
        if not args.allow_local_source:
            raise V3IngestError("local source requires --allow-local-source")
        return _copy_local_source(source, input_dir, job_id, args.name)
    validate_v3_url(source)
    name = imp.filename_from_url(source, args.name)
    temp = input_dir / f".{Path(name).stem}.{job_id}.part"
    digest = hashlib.sha256()
    total = 0
    try:
        with _open_url(source) as response, temp.open("wb") as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    raise V3IngestError("PDF exceeds 250 MB")
                out.write(chunk)
                digest.update(chunk)
        if not imp.looks_like_pdf(temp):
            raise V3IngestError("downloaded file does not start with %PDF-")
        return {
            "temp": temp,
            "source_url": source,
            "source_kind": "url",
            "source_filename": name,
            "bytes": total,
            "sha256": digest.hexdigest(),
        }
    except Exception:
        temp.unlink(missing_ok=True)
        raise


def _destinations(dest: Path, sha256: str):
    yield dest
    yield dest.with_name(f"{dest.stem}__{sha256[:8]}{dest.suffix}")
    for index in range(2, 12):
        yield dest.with_name(f"{dest.stem}__{sha256[:8]}_{index}{dest.suffix}")


def promote_no_clobber(temp: Path, dest: Path, sha256: str) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    for candidate in _destinations(dest, sha256):
        try:
            os.link(temp, candidate)
        except FileExistsError:
            if candidate.is_symlink():
                raise V3IngestError(f"refusing symlink destination: {candidate}")
            if sha256_file(candidate) == sha256:
                temp.unlink(missing_ok=True)
                return candidate
            continue
        except OSError as exc:
            if exc.errno not in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP}:
                raise
            try:
                fd = os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if candidate.is_symlink():
                    raise V3IngestError(f"refusing symlink destination: {candidate}")
                if sha256_file(candidate) == sha256:
                    temp.unlink(missing_ok=True)
                    return candidate
                continue
            try:
                with os.fdopen(fd, "wb") as out, temp.open("rb") as source:
                    while chunk := source.read(1024 * 1024):
                        out.write(chunk)
                    out.flush()
                    os.fsync(out.fileno())
            except Exception:
                candidate.unlink(missing_ok=True)
                raise
            temp.unlink(missing_ok=True)
            return candidate
        else:
            temp.unlink(missing_ok=True)
            return candidate
    raise V3IngestError(f"cannot reserve a unique PDF destination for {dest}")


def connect_v3(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def duplicate_state(conn: sqlite3.Connection, sha256: str, issue_key: str) -> dict[str, Any]:
    issue = conn.execute(
        "SELECT gazette_issue_id FROM gazette_issues WHERE issue_key = ?",
        (issue_key,),
    ).fetchone()
    issue_id = str(issue["gazette_issue_id"]) if issue else None
    same_hash = conn.execute(
        "SELECT source_document_id, gazette_issue_id FROM source_documents WHERE sha256 = ?",
        (sha256,),
    ).fetchall()
    issue_docs = conn.execute(
        "SELECT source_document_id, sha256 FROM source_documents WHERE gazette_issue_id = ?",
        (issue_id,),
    ).fetchall() if issue_id else []
    same_issue_hash = [row for row in same_hash if issue_id and row["gazette_issue_id"] == issue_id]
    return {
        "issue_id": issue_id,
        "same_hash": [dict(row) for row in same_hash],
        "same_issue_hash": [dict(row) for row in same_issue_hash],
        "issue_docs": [dict(row) for row in issue_docs],
    }


def decide_duplicate(state: dict[str, Any], args: argparse.Namespace) -> str:
    if args.reprocess and args.new_version:
        raise V3IngestError("--reprocess and --new-version are mutually exclusive")
    if (args.reprocess or args.new_version) and not (args.reason or "").strip():
        raise V3IngestError("--reason is required with --reprocess/--new-version")
    issue_docs = state["issue_docs"]
    same_issue = state["same_issue_hash"]
    same_hash = state["same_hash"]
    if issue_docs and not same_issue:
        if not args.new_version:
            raise OperatorDecision(
                "new_source_version_required",
                "same issue has different PDF content; use --new-version --reason",
            )
        return "new_version"
    if same_issue:
        if args.new_version:
            raise V3IngestError("identical PDF content; use --reprocess instead")
        if not args.reprocess:
            raise OperatorDecision(
                "already_imported",
                "same PDF is already imported for this issue",
            )
        return "reprocess_existing"
    if same_hash:
        if args.new_version:
            raise V3IngestError("cross-issue identical content; use --reprocess instead")
        if not args.reprocess:
            raise OperatorDecision(
                "already_imported_content_conflict",
                "same PDF exists under another issue; use --reprocess --reason",
            )
        return "cross_issue_reprocess"
    if args.reprocess or args.new_version:
        raise V3IngestError("decision flag has no prior document to act on")
    return "normal"


def validate_artifacts(out_dir: Path) -> tuple[dict, list, list, dict]:
    report = replay.read_json(out_dir / "run_report.json")
    documents = replay.read_csv(out_dir / "documents.csv")
    rows = replay.read_csv(out_dir / "tb_rows.csv")
    if not report:
        raise V3IngestError("OCR did not produce run_report.json")
    if not documents:
        raise V3IngestError("OCR did not produce documents.csv")
    if not (out_dir / "tb_rows.csv").is_file():
        raise V3IngestError("OCR did not produce tb_rows.csv")
    coverage = replay.validate_page_coverage(
        report, documents, rows, strict=True
    )
    expected = report.get("source_pdf_sha256")
    return report, documents, rows, coverage


def run_capture(command: list[str], log_path: Path) -> tuple[int, str, str]:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\n$ " + " ".join(command) + "\n")
        completed = subprocess.run(
            command, cwd=ROOT, text=True, capture_output=True, check=False
        )
        log.write(completed.stdout)
        log.write(completed.stderr)
    return completed.returncode, completed.stdout, completed.stderr


def run_replay(db_path: Path, out_dir: Path, issue_key: str, log_path: Path) -> dict:
    code, stdout, stderr = run_capture([
        sys.executable, str(ROOT / "tools" / "replay_runs_to_v3.py"),
        "--v3-db", str(db_path), "--run-dir", str(out_dir),
        "--issue-key", issue_key, "--no-v5-lookup",
    ], log_path)
    if code != 0:
        raise V3IngestError(f"V3 replay failed ({code}): {stderr.strip() or stdout.strip()}")
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise V3IngestError("V3 replay produced no result")
    try:
        result = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise V3IngestError("V3 replay result is not valid JSON") from exc
    if result.get("status") not in ("ok", "already_present"):
        raise V3IngestError(f"V3 replay returned {result.get('status')}")
    return result


def result_manifest_path(kind: str, job_id: str) -> Path:
    root = ROOT / ("output_import_failed" if kind == "failed" else "output_import_results")
    root.mkdir(parents=True, exist_ok=True)
    base = root / job_id
    suffix = 0
    while True:
        candidate = base if suffix == 0 else Path(f"{base}-{uuid.uuid4().hex[:4]}")
        try:
            candidate.mkdir(exist_ok=False)
            return candidate / "import_manifest.json"
        except FileExistsError:
            suffix += 1
            if suffix > 5:
                raise V3IngestError(f"cannot reserve result directory for {job_id}")


def emit(args: argparse.Namespace, result: dict, manifest_path: Path | None) -> None:
    payload = dict(result)
    status = result.get("status")
    if status == "usage_error":
        exit_code = 2
    elif status in (
        "completed", "already_imported", "download_only", "inspect_only", "planned",
    ):
        exit_code = 0
    elif status in (
        "already_imported_content_conflict", "new_source_version_required",
        "import_locked",
    ):
        exit_code = 3
    else:
        exit_code = 1
    if manifest_path:
        payload["manifest_path"] = str(manifest_path)
    payload.setdefault("manifest_path", None)
    payload.setdefault("exit_code", exit_code)
    if args.json:
        print(json.dumps(payload, ensure_ascii=False), flush=True)
    else:
        print(payload.get("message") or payload.get("status"), file=sys.stderr)
        if manifest_path:
            print(f"Manifest: {manifest_path}", file=sys.stderr)


def base_result(args: argparse.Namespace, job_id: str | None, issue_key: str | None) -> dict:
    operation = (
        "replay_only" if args.replay_only
        else "download_only" if args.download_only
        else "inspect_only" if args.inspect_only
        else "full_import"
    )
    return {
        "status": "failed",
        "operation": operation,
        "target": "v3",
        "job_id": job_id,
        "issue_key": issue_key,
        "run_dir": None,
        "manifest_path": None,
        "pdf_sha256": None,
        "v3": None,
        "error": None,
    }


def build_manifest(args, job_id, issue_key, out_dir, pdf_info, coverage, report) -> dict:
    return {
        "manifest_version": 2,
        "manifest_origin": "written",
        "target": "v3",
        "operation": "full_import",
        "job_id": job_id,
        "source_kind": pdf_info["source_kind"],
        "source_url": pdf_info["source_url"] or None,
        "source_filename": pdf_info["source_filename"],
        "issue_key": issue_key,
        "pdf_path": str(pdf_info["path"]),
        "pdf_sha256": pdf_info["sha256"],
        "pdf_bytes": pdf_info["bytes"],
        "pdf_pages": coverage["pdf_pages"],
        "ocr_pages_observed": coverage["ocr_pages_observed"],
        "ocr_pages_successful": coverage["ocr_completed_pages"],
        "ocr_failed_pages": coverage["ocr_failed_pages"],
        "ocr_skipped_pages": coverage["ocr_skipped_pages"],
        "page_coverage_status": "valid",
        "ocr_page_coverage": coverage["ocr_page_coverage"],
        "run_dir": str(out_dir),
        "v3_db": str(resolve_v3_db(args)),
        "status": "running",
        "current_stage": "v3_replay",
        "download_status": "completed",
        "ocr_status": "completed",
        "v3_replay_status": "pending",
        "started_at": now(),
        "download_finished_at": now(),
        "ocr_finished_at": now(),
        "finished_at": None,
        "decision_type": "reprocess" if args.reprocess else "new_version" if args.new_version else None,
        "decision_reason": args.reason if (args.reprocess or args.new_version) else None,
        "last_exit_code": None,
        "error_type": None,
        "error_message": None,
        "v3": {},
        "steps": {
            "ocr": {
                "command": imp.build_ocr_command(args, Path(pdf_info["path"]), out_dir) + ["--full-page-records", "--pages", "all"]
            }
        },
    }


def _cleanup_temp(pdf_info: dict | None) -> None:
    if pdf_info and pdf_info.get("temp"):
        Path(pdf_info["temp"]).unlink(missing_ok=True)


def _run_full(args: argparse.Namespace) -> int:
    job_id = job_id_for(args)
    issue_key: str | None = None
    result = base_result(args, job_id, None)
    manifest_path: Path | None = None
    v3_db = resolve_v3_db(args)
    pdf_info: dict | None = None
    out_dir: Path | None = None
    manifest: dict[str, Any] | None = None
    try:
        validate_v3_db(v3_db)
        input_dir = resolve_v3_input(args)
        input_dir.mkdir(parents=True, exist_ok=True)
        issue_key = resolve_issue_key(args, args.source)
        out_dir = resolve_v3_outdir(args, issue_key, job_id)
        if out_dir.exists():
            raise V3IngestError(f"output directory already exists: {out_dir}")
        with import_lock(v3_db):
            pdf_info = download_source(args, input_dir, job_id)
            with contextlib.closing(connect_v3(v3_db)) as conn:
                state = duplicate_state(conn, pdf_info["sha256"], issue_key)
                action = decide_duplicate(state, args)
            if action in ("normal", "reprocess_existing", "cross_issue_reprocess", "new_version"):
                dest = input_dir / pdf_info["source_filename"]
                final_pdf = promote_no_clobber(pdf_info["temp"], dest, pdf_info["sha256"])
                pdf_info["path"] = str(final_pdf)
            out_dir.mkdir(parents=True, exist_ok=False)
            log_path = out_dir / "import.log"
            ocr_cmd = imp.build_ocr_command(args, Path(pdf_info["path"]), out_dir) + [
                "--full-page-records", "--pages", "all",
            ]
            code, stdout, stderr = run_capture(ocr_cmd, log_path)
            if code != 0:
                raise V3IngestError(f"OCR failed ({code}): {stderr.strip() or stdout.strip()}")
            report, documents, rows, coverage = validate_artifacts(out_dir)
            if report.get("source_pdf_sha256") != pdf_info["sha256"]:
                raise V3IngestError("run_report SHA-256 does not match the downloaded PDF")
            manifest = build_manifest(args, job_id, issue_key, out_dir, pdf_info, coverage, report)
            manifest_path = out_dir / "import_manifest.json"
            imp.write_manifest(out_dir, manifest)
            replay_result = run_replay(v3_db, out_dir, issue_key, log_path)
            manifest["v3_replay_status"] = "completed"
            manifest["status"] = "completed"
            manifest["finished_at"] = now()
            manifest["v3"] = {
                "gazette_issue_id": replay_result.get("gazette_issue_id"),
                "source_document_id": replay_result.get("source_document_id"),
                "run_id": replay_result.get("run_id"),
                "already_present": replay_result.get("already_present"),
                "evidence_fingerprint": replay_result.get("evidence_fingerprint"),
                "run_status": replay_result.get("run_status"),
                "pages": replay_result.get("pages"),
                "page_extractions": replay_result.get("page_extractions"),
                "rows_inserted": replay_result.get("rows_inserted"),
                "rows_skipped": replay_result.get("rows_skipped"),
            }
            imp.write_manifest(out_dir, manifest)
            result.update({
                "status": "completed",
                "issue_key": issue_key,
                "run_dir": str(out_dir),
                "manifest_path": str(manifest_path),
                "pdf_sha256": pdf_info["sha256"],
                "v3": manifest["v3"],
                "message": "V3 raw OCR evidence imported — awaiting page classification",
            })
            emit(args, result, manifest_path)
            return 0
    except OperatorDecision as exc:
        _cleanup_temp(pdf_info)
        result.update({"status": exc.status, "issue_key": issue_key, "error": {
            "type": type(exc).__name__, "message": str(exc)
        }})
        manifest_path = result_manifest_path("results", job_id)
        imp.write_manifest(manifest_path.parent, {
            **result, "target": "v3", "job_id": job_id,
            "manifest_version": 2, "status": exc.status,
        })
        emit(args, result, manifest_path)
        return 0 if exc.status == "already_imported" else 3
    except ImportLocked as exc:
        _cleanup_temp(pdf_info)
        result.update({"status": "import_locked", "issue_key": issue_key, "error": {
            "type": type(exc).__name__, "message": str(exc)
        }})
        manifest_path = result_manifest_path("results", job_id)
        imp.write_manifest(manifest_path.parent, {
            **result, "target": "v3", "job_id": job_id,
            "manifest_version": 2, "status": "import_locked",
        })
        emit(args, result, manifest_path)
        return 3
    except Exception as exc:  # noqa: BLE001 - CLI boundary
        _cleanup_temp(pdf_info)
        result.update({"status": "failed", "issue_key": issue_key, "error": {
            "type": type(exc).__name__, "message": str(exc)
        }})
        if out_dir is not None and out_dir.is_dir():
            failed_manifest = manifest or {
                "manifest_version": 2,
                "manifest_origin": "written",
                "target": "v3",
                "operation": "full_import",
                "job_id": job_id,
                "issue_key": issue_key,
                "run_dir": str(out_dir),
                "started_at": now(),
                "download_status": "completed",
                "ocr_status": "failed",
                "v3_replay_status": "pending",
                "v3": {},
                "steps": {},
            }
            failed_manifest.update({
                "status": "failed",
                "finished_at": now(),
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            })
            manifest_path = out_dir / "import_manifest.json"
            imp.write_manifest(out_dir, failed_manifest)
        else:
            manifest_path = result_manifest_path("failed", job_id)
            imp.write_manifest(manifest_path.parent, {
                **result, "target": "v3", "job_id": job_id,
                "manifest_version": 2, "status": "failed",
            })
        emit(args, result, manifest_path)
        return 1


def _run_diagnostic(args: argparse.Namespace) -> int:
    job_id = job_id_for(args)
    result = base_result(args, job_id, None)
    try:
        input_dir = resolve_v3_input(args)
        input_dir.mkdir(parents=True, exist_ok=True)
        issue_key = resolve_issue_key(args, args.source)
        out_dir = resolve_v3_outdir(args, issue_key, job_id)
        if out_dir.exists():
            raise V3IngestError(f"output directory already exists: {out_dir}")
        pdf_info = download_source(args, input_dir, job_id)
        final_pdf = promote_no_clobber(
            pdf_info["temp"], input_dir / pdf_info["source_filename"], pdf_info["sha256"]
        )
        pdf_info["path"] = str(final_pdf)
        out_dir.mkdir(parents=True, exist_ok=False)
        log_path = out_dir / "import.log"
        operation = "download_only" if args.download_only else "inspect_only"
        if args.inspect_only:
            command = imp.build_ocr_command(args, Path(final_pdf), out_dir)
            code, stdout, stderr = run_capture(command, log_path)
            if code != 0:
                raise V3IngestError(f"inspect OCR failed ({code}): {stderr.strip() or stdout.strip()}")
        manifest = {
            "manifest_version": 2,
            "manifest_origin": "written",
            "target": "v3",
            "operation": operation,
            "job_id": job_id,
            "source_kind": pdf_info["source_kind"],
            "source_url": pdf_info["source_url"] or None,
            "source_filename": pdf_info["source_filename"],
            "issue_key": issue_key,
            "pdf_path": str(final_pdf),
            "pdf_sha256": pdf_info["sha256"],
            "pdf_bytes": pdf_info["bytes"],
            "run_dir": str(out_dir),
            "status": operation,
            "started_at": now(),
            "finished_at": now(),
            "v3": None,
        }
        manifest_path = out_dir / "import_manifest.json"
        imp.write_manifest(out_dir, manifest)
        result.update({
            "status": operation,
            "issue_key": issue_key,
            "run_dir": str(out_dir),
            "manifest_path": str(manifest_path),
            "pdf_sha256": pdf_info["sha256"],
            "message": f"V3 {operation} completed (no V3 ingestion)",
        })
        emit(args, result, manifest_path)
        return 0
    except Exception as exc:  # noqa: BLE001
        result.update({"status": "failed", "error": {
            "type": type(exc).__name__, "message": str(exc)
        }})
        manifest_path = result_manifest_path("failed", job_id)
        imp.write_manifest(manifest_path.parent, {
            **result, "target": "v3", "job_id": job_id,
            "manifest_version": 2, "status": "failed",
        })
        emit(args, result, manifest_path)
        return 1


def _run_replay_only(args: argparse.Namespace) -> int:
    job_id = job_id_for(args)
    result = base_result(args, job_id, None)
    if not args.outdir:
        result["status"] = "usage_error"
        result["error"] = {"type": "V3IngestError", "message": "--outdir is required with --replay-only"}
        emit(args, result, None)
        return 2
    out_dir = args.outdir.expanduser().resolve()
    manifest_path = out_dir / "import_manifest.json"
    v3_db = resolve_v3_db(args)
    manifest: dict[str, Any] = {}
    try:
        validate_v3_db(v3_db)
        manifest = replay.read_json(manifest_path)
        if not manifest:
            raise V3IngestError("replay-only requires import_manifest.json")
        if manifest.get("manifest_version") != 2 or manifest.get("target") != "v3":
            raise V3IngestError("replay-only manifest must be v2 target v3")
        operation = manifest.get("operation")
        if operation not in (None, "full_import", "replay_only"):
            raise V3IngestError(f"replay-only does not accept operation {operation}")
        if manifest.get("status") not in ("running", "failed", "completed"):
            raise V3IngestError(f"replay-only does not accept status {manifest.get('status')}")
        issue_key = replay.resolve_issue_key(out_dir, manifest, args.issue_key)
        if Path(manifest.get("run_dir", "")).resolve() != out_dir:
            raise V3IngestError("manifest run_dir does not match --outdir")
        if (
            not (out_dir / "run_report.json").is_file()
            or not (out_dir / "documents.csv").is_file()
            or not (out_dir / "tb_rows.csv").is_file()
        ):
            raise V3IngestError("replay-only artifacts are incomplete")
        report = replay.read_json(out_dir / "run_report.json")
        if report.get("source_pdf_sha256") != manifest.get("pdf_sha256"):
            raise V3IngestError("run_report SHA-256 does not match the manifest")
        pdf_path = Path(manifest.get("pdf_path", ""))
        if pdf_path.is_symlink():
            raise V3IngestError("manifest PDF path is a symlink")
        if not pdf_path.is_file() or sha256_file(pdf_path) != manifest.get("pdf_sha256"):
            raise V3IngestError("manifest PDF hash does not match the source file")
        with import_lock(v3_db):
            replay_result = run_replay(
                v3_db, out_dir, issue_key, out_dir / "replay.log"
            )
            manifest["v3_replay_status"] = "completed"
            manifest["status"] = "completed"
            manifest["operation"] = "replay_only"
            manifest["finished_at"] = now()
            manifest["v3"] = {
                "run_id": replay_result.get("run_id"),
                "source_document_id": replay_result.get("source_document_id"),
                "gazette_issue_id": replay_result.get("gazette_issue_id"),
                "already_present": replay_result.get("already_present"),
                "evidence_fingerprint": replay_result.get("evidence_fingerprint"),
            }
            imp.write_manifest(out_dir, manifest)
            result.update({
                "status": "completed",
                "operation": "replay_only",
                "issue_key": issue_key,
                "run_dir": str(out_dir),
                "manifest_path": str(manifest_path),
                "pdf_sha256": manifest.get("pdf_sha256"),
                "v3": {
                    "run_id": replay_result.get("run_id"),
                    "already_present": replay_result.get("already_present"),
                    "evidence_fingerprint": replay_result.get("evidence_fingerprint"),
                    "run_status": replay_result.get("run_status"),
                },
                "message": "V3 raw OCR evidence replayed — awaiting page classification",
            })
            emit(args, result, manifest_path)
            return 0
    except ImportLocked as exc:
        result.update({"status": "import_locked", "error": {"type": type(exc).__name__, "message": str(exc)}})
        emit(args, result, None)
        return 3
    except Exception as exc:  # noqa: BLE001
        result.update({"status": "failed", "error": {"type": type(exc).__name__, "message": str(exc)}})
        if manifest_path.is_file():
            manifest["status"] = "failed"
            manifest["v3_replay_status"] = "failed"
            manifest["finished_at"] = now()
            manifest["error_type"] = type(exc).__name__
            manifest["error_message"] = str(exc)
            imp.write_manifest(out_dir, manifest)
            emit(args, result, manifest_path)
        else:
            emit(args, result, None)
        return 1


def validate_cli(args: argparse.Namespace) -> None:
    if args.db is not None:
        raise V3IngestError("--db is the legacy V5 database option; use --v3-db")
    for name in (
        "publish", "force_publish", "no_db", "no_ocr", "no_name_map",
        "no_bank_refresh", "overwrite_pdf", "reuse_output",
    ):
        if getattr(args, name, False):
            raise V3IngestError(f"--{name.replace('_', '-')} is not valid with --target v3")
    if (
        not args.replay_only
        and not args.download_only
        and not args.inspect_only
        and args.pages
        and args.pages.strip().lower() != "all"
    ):
        raise V3IngestError("full V3 ingestion requires --pages all")
    if args.replay_only:
        if args.source:
            raise V3IngestError("--replay-only does not accept a source argument")
        if args.pages.strip().lower() != "all":
            raise V3IngestError("--replay-only does not accept --pages")
        for name in ("name", "input_dir", "engine", "vision_level"):
            if getattr(args, name):
                raise V3IngestError(f"--replay-only does not accept --{name.replace('_', '-')}")
    elif not args.source:
        raise V3IngestError("source is required unless --replay-only is used")
    if args.reprocess and args.new_version:
        raise V3IngestError("--reprocess and --new-version are mutually exclusive")
    if args.download_only and args.inspect_only:
        raise V3IngestError("--download-only and --inspect-only are mutually exclusive")
    if args.replay_only and (args.download_only or args.inspect_only):
        raise V3IngestError("--replay-only cannot be combined with diagnostic modes")
    if (args.replay_only or args.download_only or args.inspect_only) and (
        args.reprocess or args.new_version
    ):
        raise V3IngestError("decision flags are not valid in replay-only/diagnostic modes")


def _run_planned(args: argparse.Namespace) -> int:
    job_id = job_id_for(args)
    result = base_result(args, job_id, None)
    try:
        issue_key = resolve_issue_key(args, args.source) if not args.replay_only else None
        out_dir = (
            args.outdir.expanduser().resolve()
            if args.outdir else resolve_v3_outdir(args, issue_key or "2026-01", job_id)
        )
        result.update({
            "status": "planned",
            "issue_key": issue_key,
            "run_dir": str(out_dir),
            "message": "V3 import plan only; no download, OCR or V3 writes",
        })
        emit(args, result, None)
        return 0
    except Exception as exc:  # noqa: BLE001
        result.update({"status": "usage_error", "error": {
            "type": type(exc).__name__, "message": str(exc)
        }})
        emit(args, result, None)
        return 2


def run(args: argparse.Namespace) -> int:
    try:
        validate_cli(args)
    except V3IngestError as exc:
        result = base_result(args, job_id_for(args), None)
        result.update({
            "status": "usage_error",
            "error": {"type": type(exc).__name__, "message": str(exc)},
        })
        emit(args, result, None)
        return 2
    if args.dry_run:
        return _run_planned(args)
    if args.replay_only:
        return _run_replay_only(args)
    if args.download_only or args.inspect_only:
        return _run_diagnostic(args)
    return _run_full(args)
