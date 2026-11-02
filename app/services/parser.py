"""Coordinate-aware parser for onlineplanservice.com "Bid Results" PDFs.

Template facts this parser relies on (verified against the sample PDF):
  * Project titles are the only BLUE (#0000FF) text; wrapped titles span 2 lines.
  * Locations are BOLD, right-aligned (x0 > 400), near the title's y-range.
  * Bid-date headers are BOLD m/d/yyyy text placed above their project group.
    The report date at the top of page 1 is NOT bold, so it is ignored.
  * Contractor names are regular 11pt text at the left margin (x0 < 60).
  * Bid amounts are on the same row as their contractor, to the right (x0 > 280).
  * Subcontractor lists use a smaller font (< 10pt) and are ignored.
  * The PDF's internal text order is scrambled, and projects continue across
    page breaks, so the whole document is parsed as ONE stream sorted by
    (page, y, x) rather than page by page or in extraction order.

The parser never invents data: anything it cannot find is null and flagged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pymupdf

BLUE = 0x0000FF
DATE_RE = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}$")
MONEY_RE = re.compile(r"^\$?\s*[\d,]+(?:\.\d{1,2})?$")

ROW_TOLERANCE = 3.0        # contractor and amount on the same row (pt)
TITLE_WRAP_GAP = 20.0      # max y gap between lines of one wrapped title (pt)
LOCATION_MAX_DIST = 20.0   # max y distance from a location to its title (pt)
CONTRACTOR_WRAP_GAP = 16.0 # rows are ~24pt apart; a wrapped name line is closer


class ExtractionError(Exception):
    """Raised when the PDF cannot be parsed at all (e.g. scanned/image-only)."""


@dataclass
class Line:
    page: int
    y0: float
    y1: float
    x0: float
    text: str
    size: float
    color: int
    bold: bool


@dataclass
class Bidder:
    contractor_name: str
    bid_amount: Decimal | None
    amount_status: str  # "ok" | "no_price" | "missing"


@dataclass
class Project:
    project_name: str
    page: int
    y_top: float
    y_bottom: float
    project_date: str | None = None
    project_url: str | None = None
    location: str | None = None
    bidders: list[Bidder] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Low-level extraction
# --------------------------------------------------------------------------- #
def _read_lines(doc: pymupdf.Document) -> list[Line]:
    lines: list[Line] = []
    for page in doc:
        for block in page.get_text("dict")["blocks"]:
            for ln in block.get("lines", []):
                spans = [s for s in ln["spans"] if s["text"].strip()]
                if not spans:
                    continue
                text = " ".join("".join(s["text"] for s in ln["spans"]).split())
                first = spans[0]
                lines.append(
                    Line(
                        page=page.number,
                        y0=ln["bbox"][1],
                        y1=ln["bbox"][3],
                        x0=ln["bbox"][0],
                        text=text,
                        size=first["size"],
                        color=first["color"],
                        bold=bool(first["flags"] & 16),
                    )
                )
    lines.sort(key=lambda l: (l.page, round(l.y0, 1), l.x0))
    return lines


def _read_links(doc: pymupdf.Document) -> dict[int, list[tuple[pymupdf.Rect, str]]]:
    """Return usable URI links per page. Zero-width rectangles are artifacts."""
    out: dict[int, list[tuple[pymupdf.Rect, str]]] = {}
    for page in doc:
        for link in page.get_links():
            uri = link.get("uri")
            rect = pymupdf.Rect(link["from"])
            if uri and rect.width > 1 and uri.lower().startswith(("http://", "https://")):
                out.setdefault(page.number, []).append((rect, uri))
    return out


def _parse_amount(text: str) -> tuple[Decimal | None, str]:
    cleaned = text.replace("$", "").replace(",", "").replace(" ", "")
    try:
        value = Decimal(cleaned)
    except InvalidOperation:
        return None, "missing"
    if value == 0:
        # A literal 0 means "no price" (e.g. procurement listings), not a $0 bid.
        return None, "no_price"
    return value.quantize(Decimal("0.01")), "ok"


# --------------------------------------------------------------------------- #
# Classification helpers
# --------------------------------------------------------------------------- #
def _is_title(l: Line) -> bool:
    return l.color == BLUE


def _is_date(l: Line) -> bool:
    return l.bold and bool(DATE_RE.match(l.text))


def _is_location(l: Line) -> bool:
    return l.bold and l.x0 > 400 and not _is_title(l) and not DATE_RE.match(l.text)


def _is_amount(l: Line) -> bool:
    return l.x0 > 280 and not l.bold and bool(MONEY_RE.match(l.text))


def _is_contractor(l: Line) -> bool:
    return l.x0 < 60 and not l.bold and l.size >= 10 and not _is_title(l)


# --------------------------------------------------------------------------- #
# Main parser
# --------------------------------------------------------------------------- #
def parse_pdf_bytes(data: bytes) -> list[Project]:
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - PyMuPDF raises several types
        raise ExtractionError("The file could not be opened as a PDF.") from exc

    with doc:
        if doc.needs_pass:
            raise ExtractionError("The PDF is password-protected.")
        lines = _read_lines(doc)
        links = _read_links(doc)

    if not lines:
        raise ExtractionError(
            "No extractable text found. The PDF appears to be scanned or image-based; "
            "OCR support is required for this file."
        )

    projects = _build_projects(lines)
    if not projects:
        raise ExtractionError(
            "No projects found. This PDF does not match the expected Bid Results "
            "layout (project titles in blue text)."
        )

    _assign_locations(lines, projects)
    _assign_urls(links, projects)
    _assign_bidders(lines, projects)
    _validate(projects)
    return projects


def _build_projects(lines: list[Line]) -> list[Project]:
    """Create projects from blue title lines and stamp each with its bid date."""
    projects: list[Project] = []
    current_date: str | None = None
    prev_title_line: Line | None = None
    for l in lines:
        if _is_date(l):
            current_date = l.text
            continue
        if not _is_title(l):
            continue
        prev = projects[-1] if projects else None
        # Wrapped title: the next blue line sits directly below the previous one (~14pt).
        if (
            prev is not None
            and prev_title_line is not None
            and prev_title_line.page == l.page
            and 0 < l.y0 - prev_title_line.y0 < TITLE_WRAP_GAP
        ):
            prev.project_name = f"{prev.project_name} {l.text}"
            prev.y_bottom = l.y1
            prev_title_line = l
            continue
        prev_title_line = l
        projects.append(
            Project(
                project_name=l.text,
                page=l.page,
                y_top=l.y0,
                y_bottom=l.y1,
                project_date=current_date,
            )
        )
    return projects


def _assign_locations(lines: list[Line], projects: list[Project]) -> None:
    """Attach each location to the nearest title on its page by y-distance.

    Locations on wrapped titles sit above the first line or between the two
    lines, so sequential (stream-order) assignment gets them wrong.
    """
    for l in lines:
        if not _is_location(l):
            continue
        mid = (l.y0 + l.y1) / 2
        best, best_dist = None, LOCATION_MAX_DIST
        for p in projects:
            if p.page != l.page:
                continue
            dist = 0.0 if p.y_top <= mid <= p.y_bottom else min(abs(mid - p.y_top), abs(mid - p.y_bottom))
            if dist < best_dist:
                best, best_dist = p, dist
        if best is not None and best.location is None:
            best.location = l.text


def _assign_urls(links: dict[int, list[tuple[pymupdf.Rect, str]]], projects: list[Project]) -> None:
    for p in projects:
        title_rect = pymupdf.Rect(0, p.y_top, 10_000, p.y_bottom)
        best_uri, best_overlap = None, 0.0
        for rect, uri in links.get(p.page, []):
            overlap = (rect & title_rect).height if rect.intersects(title_rect) else 0.0
            if overlap > best_overlap:
                best_uri, best_overlap = uri, overlap
        p.project_url = best_uri


def _assign_bidders(lines: list[Line], projects: list[Project]) -> None:
    """Walk the stream; every contractor row belongs to the latest title above it.

    This naturally carries a project across a page break.
    """
    amounts = [l for l in lines if _is_amount(l)]
    current: Project | None = None
    title_starts = {(p.page, p.y_top): p for p in projects}
    last_row: tuple[Line, Bidder] | None = None

    for l in lines:
        key = (l.page, l.y0)
        if _is_title(l) and key in title_starts:
            current = title_starts[key]
            last_row = None
            continue
        if current is None or not _is_contractor(l):
            continue

        amount_line = next(
            (a for a in amounts if a.page == l.page and abs(a.y0 - l.y0) < ROW_TOLERANCE),
            None,
        )
        if amount_line is None and last_row is not None:
            prev_line, prev_bidder = last_row
            if prev_line.page == l.page and 0 < l.y0 - prev_line.y0 < CONTRACTOR_WRAP_GAP:
                # Wrapped contractor name: continuation of the previous row.
                prev_bidder.contractor_name = f"{prev_bidder.contractor_name} {l.text}"
                continue

        if amount_line is None:
            bidder = Bidder(l.text, None, "missing")
        else:
            value, status = _parse_amount(amount_line.text)
            bidder = Bidder(l.text, value, status)
        current.bidders.append(bidder)
        last_row = (l, bidder)



def _validate(projects: list[Project]) -> None:
    for p in projects:
        if not p.bidders:
            p.flags.append("no_bidders")
        if p.location is None:
            p.flags.append("missing_location")
        if p.project_date is None:
            p.flags.append("missing_date")
        if p.project_url is None:
            p.flags.append("missing_url")
        if any(b.amount_status == "missing" for b in p.bidders):
            p.flags.append("bidder_missing_amount")
        if p.bidders and all(b.amount_status == "no_price" for b in p.bidders):
            p.flags.append("no_prices_listed")
        priced = [b for b in p.bidders if b.bid_amount is not None]
        if priced and p.bidders[0].bid_amount is not None:
            lowest = min(priced, key=lambda b: b.bid_amount)
            if lowest is not p.bidders[0] and lowest.bid_amount < p.bidders[0].bid_amount:
                p.flags.append("first_listed_not_lowest")


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def to_records(projects: list[Project]) -> list[dict]:
    records = []
    for p in projects:
        first = p.bidders[0] if p.bidders else None
        priced = [b for b in p.bidders if b.bid_amount is not None]
        lowest = min(priced, key=lambda b: b.bid_amount) if priced else None
        records.append(
            {
                "project_date": p.project_date,
                "project_name": p.project_name,
                "project_url": p.project_url,
                "location": p.location,
                "first_contractor": first.contractor_name if first else None,
                "bid_amount": _num(first.bid_amount) if first else None,
                "lowest_contractor": lowest.contractor_name if lowest else None,
                "lowest_amount": _num(lowest.bid_amount) if lowest else None,
                "bidder_count": len(p.bidders),
                "bidders": [
                    {
                        "contractor_name": b.contractor_name,
                        "bid_amount": _num(b.bid_amount),
                        "amount_status": b.amount_status,
                    }
                    for b in p.bidders
                ],
                "flags": p.flags,
            }
        )
    return records
