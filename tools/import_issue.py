#!/usr/bin/env python3
"""Download a Gazette PDF, run OCR, import the run into SQLite, remap names.

Typical use::

    python tools/import_issue.py \
        https://images.bo.dsaj.gov.mo//bo/ii/2026/33/anotariais-33-2026.pdf \
        --db data/macau_balancete.sqlite --publish

The PDF is saved under ``input/`` and every OCR run gets its own new output
directory. Existing PDFs/output folders are never overwritten unless the
explicit overwrite flags are used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MAX_PDF_BYTES = 250 * 1024 * 1024


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def looks_like_pdf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def filename_from_url(url: str, explicit: str | None = None) -> str:
    if explicit:
        name = Path(explicit).name
    else:
        name = Path(unquote(urlparse(url).path)).name
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    if not name.lower().endswith(".pdf"):
        raise ValueError(f"URL does not end with a .pdf filename: {url}")
    return name


def output_dir_for(pdf_path: Path) -> Path:
    stem = pdf_path.stem
    match = re.fullmatch(r"anotariais-(\d+)-(\d{4})", stem, re.I)
    if match:
        return ROOT / "outputs" / "runs" / f"issue{match.group(1)}_{match.group(2)}"
    return ROOT / "outputs" / "runs" / stem


def _download_to_temp(url: str, temp_path: Path) -> tuple[int, str]:
    request = Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 macau-balancete-ocr/0.6"},
    )
    digest = hashlib.sha256()
    total = 0
    temp_path.parent.mkdir(parents=True, exist_ok=True)
    with urlopen(request, timeout=120) as response, temp_path.open("wb") as out:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_PDF_BYTES:
                raise RuntimeError(
                    f"PDF is larger than {MAX_PDF_BYTES // (1024 * 1024)} MB"
                )
            out.write(chunk)
            digest.update(chunk)
    return total, digest.hexdigest()


def fetch_pdf(
    source: str,
    input_dir: Path,
    *,
    explicit_name: str | None = None,
    overwrite_pdf: bool = False,
) -> dict[str, Any]:
    input_dir.mkdir(parents=True, exist_ok=True)
    parsed = urlparse(source)
    is_url = parsed.scheme in {"http", "https"}
    if not is_url:
        local = Path(source).expanduser()
        if not local.is_file():
            raise FileNotFoundError(f"PDF not found: {source}")
        name = explicit_name or local.name
        dest = input_dir / filename_from_url(local.as_uri(), name)
        if dest.exists() and dest.resolve() != local.resolve():
            if sha256_file(dest) == sha256_file(local):
                return {
                    "path": str(dest), "sha256": sha256_file(dest),
                    "downloaded": False, "reused": True,
                }
            if not overwrite_pdf:
                raise FileExistsError(
                    f"{dest} exists with different bytes; use --overwrite-pdf "
                    "or --name"
                )
        if dest.resolve() != local.resolve():
            shutil.copy2(local, dest)
        if not looks_like_pdf(dest):
            raise ValueError(f"not a PDF: {dest}")
        return {
            "path": str(dest), "sha256": sha256_file(dest),
            "downloaded": False, "reused": True,
        }

    name = filename_from_url(source, explicit_name)
    dest = input_dir / name
    temp = input_dir / f".{name}.part"
    try:
        size, downloaded_sha = _download_to_temp(source, temp)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    if not looks_like_pdf(temp):
        temp.unlink(missing_ok=True)
        raise ValueError("downloaded file does not start with %PDF-")
    if dest.exists():
        existing_sha = sha256_file(dest)
        if existing_sha == downloaded_sha:
            temp.unlink(missing_ok=True)
            return {
                "path": str(dest), "sha256": existing_sha,
                "downloaded": False, "reused": True, "bytes": dest.stat().st_size,
            }
        if not overwrite_pdf:
            temp.unlink(missing_ok=True)
            raise FileExistsError(
                f"{dest} already exists with different content; use "
                "--overwrite-pdf only after confirming the new PDF is correct"
            )
    temp.replace(dest)
    return {
        "path": str(dest), "sha256": downloaded_sha,
        "downloaded": True, "reused": False, "bytes": size,
    }


def run_command(
    command: list[str],
    log_path: Path,
    *,
    dry_run: bool = False,
) -> None:
    printable = " ".join(command)
    print(f"\n$ {printable}", flush=True)
    if dry_run:
        return
    log_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.time()
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n$ {printable}\n")
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
        code = process.wait()
        log.write(f"[exit {code}, {time.time() - started:.1f}s]\n")
    if code != 0:
        raise RuntimeError(f"command failed with exit code {code}: {printable}")


def _db_exists(db_path: Path) -> bool:
    if not db_path.exists():
        return False
    try:
        conn = sqlite3.connect(db_path)
        try:
            return bool(conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' "
                "AND name='standard_labels'"
            ).fetchone())
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def write_manifest(out_dir: Path, manifest: dict[str, Any]) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "import_manifest.json"
    path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download a Gazette PDF, OCR it and import into SQLite"
    )
    parser.add_argument("source", help="PDF URL or local PDF path")
    parser.add_argument("--db", type=Path,
                        default=Path("data/macau_balancete.sqlite"))
    parser.add_argument("--input-dir", type=Path, default=Path("input"))
    parser.add_argument("--outdir", type=Path,
                        help="output directory (default: derived from filename)")
    parser.add_argument("--name", help="override downloaded PDF filename")
    parser.add_argument("--pages", default="all",
                        help="page range passed to OCR (default: all)")
    parser.add_argument("--engine", choices=["vision", "paddle", "tesseract"])
    parser.add_argument("--vision-level", choices=["fast", "accurate"])
    parser.add_argument("--ocr-langs", default="auto")
    parser.add_argument("--publish", action="store_true",
                        help="publish the imported run after validation")
    parser.add_argument("--force-publish", action="store_true")
    parser.add_argument("--published-by", default="operator")
    parser.add_argument("--no-db", action="store_true")
    parser.add_argument("--no-ocr", action="store_true")
    parser.add_argument("--no-name-map", action="store_true")
    parser.add_argument("--no-bank-refresh", action="store_true")
    parser.add_argument("--inspect-only", action="store_true",
                        help="download and inspect only; do not OCR/import")
    parser.add_argument("--download-only", action="store_true")
    parser.add_argument("--overwrite-pdf", action="store_true",
                        help="replace an existing input PDF with different bytes")
    parser.add_argument("--reuse-output", action="store_true",
                        help="allow using an existing output directory")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    started_at = now()
    manifest: dict[str, Any] = {
        "source": args.source,
        "started_at": started_at,
        "status": "running",
        "steps": {},
    }
    try:
        pdf_info = fetch_pdf(
            args.source,
            args.input_dir,
            explicit_name=args.name,
            overwrite_pdf=args.overwrite_pdf,
        )
        pdf_path = Path(pdf_info["path"])
        out_dir = (args.outdir or output_dir_for(pdf_path)).expanduser().resolve()
        manifest.update({
            "pdf": str(pdf_path),
            "pdf_sha256": pdf_info["sha256"],
            "pdf_bytes": pdf_info.get("bytes") or pdf_path.stat().st_size,
            "outdir": str(out_dir),
            "db": str(args.db.expanduser().resolve()),
        })
        manifest["steps"]["download"] = {
            "status": "reused" if pdf_info.get("reused") else "downloaded",
            "path": str(pdf_path),
            "sha256": pdf_info["sha256"],
        }
        print(f"PDF: {pdf_path}")
        print(f"SHA-256: {pdf_info['sha256']}")
        print(f"Output: {out_dir}")
        if args.download_only:
            manifest["status"] = "download_only"
            manifest["finished_at"] = now()
            write_manifest(out_dir, manifest)
            print(f"Manifest: {out_dir / 'import_manifest.json'}")
            return 0

        if out_dir.exists() and not args.reuse_output:
            raise FileExistsError(
                f"output directory already exists: {out_dir}\n"
                "Use a new --outdir or --reuse-output explicitly."
            )
        out_dir.mkdir(parents=True, exist_ok=True)
        log_path = out_dir / "import.log"
        ocr_cmd = [
            sys.executable, str(ROOT / "macau_tb_ocr.py"), str(pdf_path),
            "--outdir", str(out_dir), "--pages", args.pages,
            "--ocr-langs", args.ocr_langs,
        ]
        if args.engine:
            ocr_cmd += ["--engine", args.engine]
        if args.vision_level:
            ocr_cmd += ["--vision-level", args.vision_level]
        if args.inspect_only:
            ocr_cmd.append("--inspect-only")
        if args.no_ocr:
            manifest["steps"]["ocr"] = {"status": "skipped"}
        else:
            run_command(ocr_cmd, log_path, dry_run=args.dry_run)
            manifest["steps"]["ocr"] = {"status": "completed", "command": ocr_cmd}
        if args.inspect_only:
            manifest["status"] = "inspect_only"
            manifest["finished_at"] = now()
            write_manifest(out_dir, manifest)
            print(f"Manifest: {out_dir / 'import_manifest.json'}")
            return 0

        documents = out_dir / "documents.csv"
        if not documents.exists():
            raise RuntimeError(f"OCR did not produce documents.csv: {documents}")

        if not args.no_db:
            db_path = args.db.expanduser().resolve()
            import_cmd = [
                sys.executable, str(ROOT / "tools" / "build_database.py"),
                str(out_dir), "--db", str(db_path),
            ]
            if args.publish:
                import_cmd += ["--publish", "--published-by", args.published_by]
            if args.force_publish:
                import_cmd.append("--force-publish")
            run_command(import_cmd, log_path, dry_run=args.dry_run)
            manifest["steps"]["db_import"] = {
                "status": "completed", "command": import_cmd
            }

            if not args.no_bank_refresh:
                bank_cmd = [
                    sys.executable, str(ROOT / "tools" / "name_registry.py"),
                    "refresh-banks", "--db", str(db_path),
                ]
                run_command(bank_cmd, log_path, dry_run=args.dry_run)
                manifest["steps"]["bank_refresh"] = {
                    "status": "completed", "command": bank_cmd
                }

            if not args.no_name_map and _db_exists(db_path):
                map_cmd = [
                    sys.executable,
                    str(ROOT / "tools" / "import_user_standard.py"),
                    "map", "--db", str(db_path),
                ]
                run_command(map_cmd, log_path, dry_run=args.dry_run)
                manifest["steps"]["name_map"] = {
                    "status": "completed", "command": map_cmd
                }

        manifest["status"] = "completed"
        manifest["finished_at"] = now()
        write_manifest(out_dir, manifest)
        print(f"\nManifest: {out_dir / 'import_manifest.json'}")
        print(f"Log:      {out_dir / 'import.log'}")
        return 0
    except (OSError, RuntimeError, ValueError, sqlite3.Error) as exc:
        manifest["status"] = "failed"
        manifest["error"] = str(exc)
        manifest["finished_at"] = now()
        try:
            out_dir = (args.outdir or ROOT / "output_import_failed").resolve()
            write_manifest(out_dir, manifest)
        except OSError:
            pass
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
