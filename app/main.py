"""FastAPI entry point for the PDF Bid Results Extractor.

Run:  uvicorn app.main:app --reload
Then open http://127.0.0.1:8000
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field, ValidationError
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.services.parser import ExtractionError, parse_pdf_bytes, to_records
from app.services.eml_parser import EmailExtractionError, parse_eml_bytes, to_email_records
from app.services.pdf_report import build_email_report_pdf, build_report_pdf

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB
MAX_REPORT_JSON_BYTES = 5 * 1024 * 1024  # 5 MB of extraction JSON per PDF report
STATIC_DIR = Path(__file__).parent / "static"

log = logging.getLogger("bid_extractor")
app = FastAPI(title="PDF Bid Results Extractor", version="1.0.0")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/api/extract")
async def extract(file: UploadFile = File(...)) -> dict:
    filename = Path(file.filename or "upload.pdf").name  # never echo client paths
    if not filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only .pdf files are accepted.")

    # Read at most limit+1 bytes so oversize uploads are rejected without buffering them fully.
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 20 MB limit.")
    if not data.startswith(b"%PDF-"):
        raise HTTPException(status_code=400, detail="The file is not a valid PDF (missing %PDF header).")

    # Parsing is CPU-bound; keep it off the event loop. Nothing is written to disk.
    try:
        projects = await run_in_threadpool(parse_pdf_bytes, data)
    except ExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001
        log.exception("Unexpected failure while parsing %s", filename)
        raise HTTPException(status_code=500, detail="The PDF could not be processed.")

    records = to_records(projects)
    return {
        "source_file": filename,
        "total_projects": len(records),
        "total_bidders": sum(r["bidder_count"] for r in records),
        "flagged_projects": sum(1 for r in records if r["flags"]),
        "projects": records,
    }


# --------------------------------------------------------------------------- #
# PDF report export
# --------------------------------------------------------------------------- #
class BidderIn(BaseModel):
    contractor_name: str = Field(max_length=500)
    bid_amount: float | None = None
    amount_status: str = Field(default="ok", max_length=20)


class ProjectIn(BaseModel):
    project_date: str | None = Field(default=None, max_length=20)
    project_name: str = Field(max_length=1000)
    project_url: str | None = Field(default=None, max_length=2000)
    location: str | None = Field(default=None, max_length=200)
    first_contractor: str | None = Field(default=None, max_length=500)
    bid_amount: float | None = None
    lowest_contractor: str | None = Field(default=None, max_length=500)
    lowest_amount: float | None = None
    bidder_count: int = Field(default=0, ge=0, le=10_000)
    bidders: list[BidderIn] = Field(default_factory=list, max_length=1000)
    flags: list[str] = Field(default_factory=list, max_length=20)


class ReportIn(BaseModel):
    source_file: str = Field(default="bid-results.pdf", max_length=300)
    total_bidders: int = Field(default=0, ge=0)
    flagged_projects: int = Field(default=0, ge=0)
    projects: list[ProjectIn] = Field(max_length=5000)


@app.post("/api/export/pdf")
async def export_pdf(request: Request) -> Response:
    raw = await request.body()
    if len(raw) > MAX_REPORT_JSON_BYTES:
        raise HTTPException(status_code=413, detail="Report data is too large.")
    try:
        report = ReportIn.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid report data.") from exc

    pdf = await run_in_threadpool(build_report_pdf, report.model_dump())
    stem = Path(report.source_file).stem or "bid-results"
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "-" for c in stem)[:80]
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_stem}-report.pdf"'},
    )


# --------------------------------------------------------------------------- #
# Daily Email Report (.eml)
# --------------------------------------------------------------------------- #
@app.post("/api/extract-eml")
async def extract_eml(file: UploadFile = File(...)) -> dict:
    filename = Path(file.filename or "report.eml").name
    if not filename.lower().endswith(".eml"):
        raise HTTPException(status_code=400, detail="Only .eml files (saved emails) are accepted.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds the 20 MB limit.")
    if not data.strip():
        raise HTTPException(status_code=400, detail="The file is empty.")

    try:
        projects, meta = await run_in_threadpool(parse_eml_bytes, data)
    except EmailExtractionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception:  # noqa: BLE001
        log.exception("Unexpected failure while parsing %s", filename)
        raise HTTPException(status_code=500, detail="The email could not be processed.")

    records = to_email_records(projects)
    categories = list(dict.fromkeys(r["category"] for r in records))
    return {
        "source_file": filename,
        "subject": meta.get("subject"),
        "report_date": meta.get("report_date"),
        "total_projects": len(records),
        "unique_projects": len({r["bid_package_id"] or r["project_url"] for r in records}),
        "categories": [{"name": c, "count": sum(1 for r in records if r["category"] == c)} for c in categories],
        "flagged_projects": sum(1 for r in records if r["flags"]),
        "projects": records,
    }


class EmailProjectIn(BaseModel):
    category: str | None = Field(default=None, max_length=300)
    area: str | None = Field(default=None, max_length=300)
    project_name: str = Field(max_length=1000)
    project_url: str | None = Field(default=None, max_length=2000)
    project_no: str | None = Field(default=None, max_length=100)
    bid_package_id: str | None = Field(default=None, max_length=50)
    bid_date: str | None = Field(default=None, max_length=30)
    location: str | None = Field(default=None, max_length=300)
    bin_no: str | None = Field(default=None, max_length=100)
    source: str | None = Field(default=None, max_length=500)
    also_in: list[str] = Field(default_factory=list, max_length=20)
    flags: list[str] = Field(default_factory=list, max_length=20)


class EmailReportIn(BaseModel):
    source_file: str = Field(default="daily-report.eml", max_length=300)
    report_date: str | None = Field(default=None, max_length=30)
    projects: list[EmailProjectIn] = Field(max_length=5000)


@app.post("/api/export/eml-pdf")
async def export_eml_pdf(request: Request) -> Response:
    raw = await request.body()
    if len(raw) > MAX_REPORT_JSON_BYTES:
        raise HTTPException(status_code=413, detail="Report data is too large.")
    try:
        report = EmailReportIn.model_validate_json(raw)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail="Invalid report data.") from exc

    pdf = await run_in_threadpool(build_email_report_pdf, report.model_dump())
    stem = Path(report.source_file).stem or "daily-report"
    safe_stem = "".join(c if c.isalnum() or c in "-_" else "-" for c in stem)[:80]
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_stem}-report.pdf"'},
    )
