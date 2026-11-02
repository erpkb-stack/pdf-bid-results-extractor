"""Render extraction results as branded, landscape PDF reports.

Two reports share one look (logo, red rule, footer, page numbers):
  * build_report_pdf()        - PDF Bid Results tab
  * build_email_report_pdf()  - Daily Email Report (.eml) tab

Summary tables are drawn by app.services.pdf_table (exact column widths, rows
never split, headings repeat). All values come from the client, so every string
is HTML-escaped and only http(s) URLs become links.
"""
from __future__ import annotations

import html
import io
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pymupdf

from app.services.pdf_table import TABLE_CSS, Column, Layout, Row, Section

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PAGE = pymupdf.paper_rect("letter-l")  # 792 x 612 pt, landscape
MARGIN = 36
HEADER_H = 62
CONTENT = pymupdf.Rect(MARGIN, MARGIN + HEADER_H, PAGE.width - MARGIN, PAGE.height - MARGIN - 18)
FOOTER_TEXT = "© BIM Collaboration LLC, 2021 - 2026  ·  801-792-8999  ·  contact@bimcollaboration.net"

FLAG_LABELS = {
    "first_listed_not_lowest": "first listed is not lowest",
    "no_prices_listed": "no prices listed",
    "no_bidders": "no bidders",
    "missing_location": "no location",
    "missing_date": "no date",
    "missing_url": "no link",
    "bidder_missing_amount": "bidder missing amount",
    "missing_project_no": "no project number",
    "missing_bid_date": "no bid date",
    "missing_source": "no source",
}

# CSS for the bid appendix (laid out as keep-together Story blocks; no links there).
APPENDIX_CSS = """
* { font-family: sans-serif; }
body { font-size: 8.5pt; color: #1b1f24; margin: 0; }
h2 { font-size: 11pt; color: #170a92; margin: 0 0 4pt 0; }
p.meta { color: #5f6b7a; margin: 0 0 8pt 0; }
table { border-collapse: collapse; }
td { border-bottom: 0.5pt solid #dfe3e8; vertical-align: top; }
p.grp { background-color: #eeedfa; color: #170a92; font-weight: bold; padding: 3pt 4pt; margin: 0; }
table.bids { width: 62%; margin: 0; }
table.bids td { padding: 2pt 4pt; }
td.rank { width: 18pt; color: #5f6b7a; }
td.num { text-align: right; }
td.muted { color: #5f6b7a; }
td.low { font-weight: bold; }
"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _money(value) -> str:
    if value is None:
        return "—"
    return f"${float(value):,.2f}"


def _safe_url(url) -> str | None:
    if not isinstance(url, str):
        return None
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else None


def _name_cell(name, url: str | None, notes: list[str]) -> str:
    cls = "link" if url else ""
    out = f"<span class='{cls}'><b>{_e(name)}</b></span>"
    for n in notes:
        out += f"<br/><span class='flag'>{_e(n)}</span>"
    return out


def _new_layout() -> Layout:
    layout = Layout(page=PAGE, content=CONTENT, css=TABLE_CSS)
    layout.new_page()
    return layout


# --------------------------------------------------------------------------- #
# PDF Bid Results report
# --------------------------------------------------------------------------- #
BID_COLUMNS = [
    Column("Project name", 0.31), Column("Location", 0.09), Column("First contractor", 0.16),
    Column("Bid amount", 0.10, "right"), Column("Lowest bidder", 0.17), Column("Bid date", 0.09),
    Column("Bidders", 0.08, "right"),
]


def _bid_summary_rows(projects: list[dict]) -> list[Row]:
    rows = []
    for p in projects:
        url = _safe_url(p.get("project_url"))
        notes = [f"FLAG: {FLAG_LABELS.get(f, f)}" for f in p.get("flags") or []]
        first = p.get("first_contractor")
        low = p.get("lowest_contractor")
        if low and low != first:
            low_cell = f"<span class='diff'>{_e(low)} ({_money(p.get('lowest_amount'))})</span>"
        elif low:
            low_cell = "<span class='muted'>same as first</span>"
        else:
            low_cell = "—"
        rows.append(Row(
            cells=[
                _name_cell(p.get("project_name"), url, notes),
                _e(p.get("location") or "—"),
                _e(first or "—"),
                _money(p.get("bid_amount")),
                low_cell,
                _e(p.get("project_date") or "—"),
                _e(p.get("bidder_count", 0)),
            ],
            link=url,
        ))
    return rows


def _bid_tables(projects: list[dict]) -> list[str]:
    """One compact HTML block per project, laid out so a block never splits across pages."""
    blocks = []
    for p in projects:
        header = f"{p.get('project_name') or ''} · {p.get('location') or '—'} · {p.get('project_date') or '—'}"
        rows = []
        bidders = p.get("bidders") or []
        if not bidders:
            rows.append("<tr><td></td><td class='muted'>No bidders found</td><td></td></tr>")
        lowest = p.get("lowest_amount")
        for i, b in enumerate(bidders, 1):
            status = b.get("amount_status")
            amount = "no price" if status == "no_price" else "missing" if status == "missing" else _money(b.get("bid_amount"))
            is_low = lowest is not None and b.get("bid_amount") == lowest
            cls = " class='low'" if is_low else ""
            rows.append(
                f"<tr><td class='rank'>{i}</td><td{cls}>{_e(b.get('contractor_name'))}</td>"
                f"<td class='num{' low' if is_low else ''}'>{_e(amount)}</td></tr>"
            )
        blocks.append(f"<p class='grp'>{_e(header)}</p><table class='bids'>{''.join(rows)}</table>")
    return blocks


def _layout_keep_together(blocks: list[str], content: pymupdf.Rect, css: str) -> pymupdf.Document:
    """Place HTML blocks top to bottom; a block that does not fit moves to a new page whole.

    A block taller than a full page is allowed to continue across pages.
    """
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    device = writer.begin_page(PAGE)
    y = content.y0
    gap = 10

    for html_block in blocks:
        story = pymupdf.Story(html=html_block, user_css=css)
        more, filled = story.place(pymupdf.Rect(content.x0, y, content.x1, content.y1))
        if more and y > content.y0:
            writer.end_page()
            device = writer.begin_page(PAGE)
            y = content.y0
            story = pymupdf.Story(html=html_block, user_css=css)
            more, filled = story.place(content)
        story.draw(device)
        while more:  # only for a block taller than a whole page
            writer.end_page()
            device = writer.begin_page(PAGE)
            more, filled = story.place(content)
            story.draw(device)
        y = pymupdf.Rect(filled).y1 + gap

    writer.end_page()
    writer.close()
    return pymupdf.open("pdf", buffer.getvalue())


def build_report_pdf(result: dict) -> bytes:
    projects = result.get("projects") or []
    generated = datetime.now().strftime("%b %d, %Y %I:%M %p")

    layout = _new_layout()
    layout.paragraph("<h1>PDF Bid Results</h1>", gap=2)
    layout.paragraph(
        f"<p class='meta'>Source: {_e(result.get('source_file'))} · {len(projects)} projects · "
        f"{_e(result.get('total_bidders', 0))} bid rows · {_e(result.get('flagged_projects', 0))} flagged · "
        f"Generated {_e(generated)}</p>",
        gap=8,
    )
    layout.section(Section(columns=BID_COLUMNS, rows=_bid_summary_rows(projects),
                           continued_label="Project summary"))
    doc = layout.render()

    appendix_blocks = [
        "<h2>All bids by project</h2>"
        "<p class='meta'>Every bid row in listed order. The lowest priced bid in each project is in bold.</p>",
        *_bid_tables(projects),
    ]
    appendix_doc = _layout_keep_together(appendix_blocks, CONTENT, APPENDIX_CSS)
    doc.insert_pdf(appendix_doc)
    appendix_doc.close()
    return _finish(doc, "PDF Bid Results")


# --------------------------------------------------------------------------- #
# Daily Email Report (.eml)
# --------------------------------------------------------------------------- #
EMAIL_COLUMNS = [
    Column("Project name", 0.31), Column("Project no.", 0.11), Column("Bid date", 0.08),
    Column("Location", 0.14), Column("Bin #", 0.08), Column("Source", 0.28),
]


def _email_rows(projects: list[dict]) -> list[Row]:
    rows = []
    area = object()
    for p in projects:
        band = None
        if p.get("area") != area:
            area = p.get("area")
            band = _e(area) if area else None
        url = _safe_url(p.get("project_url"))
        notes = []
        if p.get("also_in"):
            notes.append("Also in: " + ", ".join(p["also_in"]))
        notes += [f"FLAG: {FLAG_LABELS.get(f, f)}" for f in p.get("flags") or []]
        rows.append(Row(
            cells=[
                _name_cell(p.get("project_name"), url, notes),
                _e(p.get("project_no") or "—"),
                _e(p.get("bid_date") or "—"),
                _e(p.get("location") or "—"),
                _e(p.get("bin_no") or "—"),
                _e(p.get("source") or "—"),
            ],
            link=url,
            band=band,
        ))
    return rows


def build_email_report_pdf(result: dict) -> bytes:
    projects = result.get("projects") or []
    generated = datetime.now().strftime("%b %d, %Y %I:%M %p")
    categories = list(dict.fromkeys(p.get("category") or "Uncategorized" for p in projects))
    report_date = result.get("report_date")

    layout = _new_layout()
    layout.paragraph("<h1>Daily Email Report</h1>", gap=2)
    layout.paragraph(
        f"<p class='meta'>Source: {_e(result.get('source_file'))}"
        f"{' · Report date ' + _e(report_date) if report_date else ''} · "
        f"{len(projects)} projects in {len(categories)} categories · Generated {_e(generated)}</p>",
        gap=10,
    )
    for c in categories:
        group = [p for p in projects if (p.get("category") or "Uncategorized") == c]
        layout.section(Section(
            columns=EMAIL_COLUMNS,
            rows=_email_rows(group),
            title=f"<h2>{_e(c)} <span class='count'>({len(group)})</span></h2>",
            continued_label=_e(c),
        ))
    return _finish(layout.render(), "Daily Email Report")


# --------------------------------------------------------------------------- #
# Shared page furniture
# --------------------------------------------------------------------------- #
def _finish(doc: pymupdf.Document, title: str) -> bytes:
    """Stamp logo, red rule, footer and page numbers on every page; return PDF bytes."""
    logo = STATIC_DIR / "logo.png"
    logo_bytes = logo.read_bytes() if logo.exists() else None
    total = doc.page_count
    grey = (0.37, 0.42, 0.48)
    for page in doc:
        if logo_bytes:
            page.insert_image(pymupdf.Rect(MARGIN, MARGIN - 8, MARGIN + 165, MARGIN + 47), stream=logo_bytes)
        y_rule = MARGIN + HEADER_H - 10
        page.draw_line((MARGIN, y_rule), (PAGE.width - MARGIN, y_rule), color=(0.655, 0.039, 0.039), width=1.5)
        page.insert_text((MARGIN, PAGE.height - MARGIN + 4), FOOTER_TEXT, fontsize=7, color=grey)
        label = f"Page {page.number + 1} of {total}"
        width = pymupdf.get_text_length(label, fontsize=7)
        page.insert_text((PAGE.width - MARGIN - width, PAGE.height - MARGIN + 4), label, fontsize=7, color=grey)

    doc.set_metadata({"title": title, "author": "BIM Collaboration LLC", "creator": "Bid Results Extractor"})
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data
