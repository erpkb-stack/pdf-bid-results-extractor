"""Render extraction results as a branded, landscape PDF report.

Uses PyMuPDF's Story API (HTML/CSS -> PDF), so no extra dependency is needed.
All values come from the client, so every string is HTML-escaped and only
http(s) URLs become links.
"""
from __future__ import annotations

import html
import io
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import pymupdf

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PAGE = pymupdf.paper_rect("letter-l")  # 792 x 612 pt, landscape
MARGIN = 36
FOOTER_TEXT = "© BIM Collaboration LLC, 2021 - 2026  ·  801-792-8999  ·  contact@bimcollaboration.net"

FLAG_LABELS = {
    "first_listed_not_lowest": "first listed is not lowest",
    "no_prices_listed": "no prices listed",
    "no_bidders": "no bidders",
    "missing_location": "no location",
    "missing_date": "no date",
    "missing_url": "no link",
    "bidder_missing_amount": "bidder missing amount",
}

CSS = """
* { font-family: sans-serif; }
body { font-size: 8.5pt; color: #1b1f24; margin: 0; }
h1 { font-size: 15pt; color: #170a92; margin: 0 0 2pt 0; }
h2 { font-size: 11pt; color: #170a92; margin: 12pt 0 4pt 0; }
p.meta { color: #5f6b7a; margin: 0 0 8pt 0; }
table { border-collapse: collapse; width: 100%; }
th { background-color: #170a92; color: #ffffff; text-align: left; padding: 3pt 4pt; font-size: 7.5pt; }
td { border-bottom: 0.5pt solid #dfe3e8; padding: 3pt 4pt; vertical-align: top; }
td.num, th.num { text-align: right; }
a { color: #170a92; text-decoration: none; font-weight: bold; }
span.flag { color: #9a5b00; font-size: 7pt; font-weight: bold; }
td.muted { color: #5f6b7a; }
td.diff { color: #9a5b00; font-weight: bold; }
p.grp { background-color: #eeedfa; color: #170a92; font-weight: bold; padding: 3pt 4pt; margin: 0; }
table.bids { width: 62%; margin: 0; }
table.bids td { padding: 2pt 4pt; }
td.rank { width: 18pt; color: #5f6b7a; }
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


def _summary_rows(projects: list[dict]) -> str:
    rows = []
    for p in projects:
        url = _safe_url(p.get("project_url"))
        name = f'<a href="{_e(url)}">{_e(p.get("project_name"))}</a>' if url else f"<b>{_e(p.get('project_name'))}</b>"
        flags = "".join(
            f'<br/><span class="flag">FLAG: {_e(FLAG_LABELS.get(f, f))}</span>' for f in p.get("flags") or []
        )
        first = p.get("first_contractor")
        low = p.get("lowest_contractor")
        if low and low != first:
            low_td = f'<td class="diff">{_e(low)} ({_money(p.get("lowest_amount"))})</td>'
        elif low:
            low_td = '<td class="muted">same as first</td>'
        else:
            low_td = "<td>—</td>"
        rows.append(
            "<tr>"
            f"<td>{name}{flags}</td>"
            f"<td>{_e(p.get('location') or '—')}</td>"
            f"<td>{_e(first or '—')}</td>"
            f"<td class='num'>{_money(p.get('bid_amount'))}</td>"
            f"{low_td}"
            f"<td>{_e(p.get('project_date') or '—')}</td>"
            f"<td class='num'>{_e(p.get('bidder_count', 0))}</td>"
            "</tr>"
        )
    return "".join(rows)


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
        blocks.append(
            f"<p class='grp'>{_e(header)}</p>"
            f"<table class='bids'>{''.join(rows)}</table>"
        )
    return blocks


def _layout_keep_together(blocks: list[str], content: pymupdf.Rect) -> pymupdf.Document:
    """Place HTML blocks top to bottom; a block that does not fit moves to a new page whole.

    A block taller than a full page is allowed to continue across pages.
    """
    buffer = io.BytesIO()
    writer = pymupdf.DocumentWriter(buffer)
    device = writer.begin_page(PAGE)
    y = content.y0
    gap = 10

    for html_block in blocks:
        story = pymupdf.Story(html=html_block, user_css=CSS)
        more, filled = story.place(pymupdf.Rect(content.x0, y, content.x1, content.y1))
        if more and y > content.y0:
            # Does not fit in what is left of this page: start a fresh page and retry.
            writer.end_page()
            device = writer.begin_page(PAGE)
            y = content.y0
            story = pymupdf.Story(html=html_block, user_css=CSS)
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
    body = f"""
    <h1>PDF Bid Results</h1>
    <p class="meta">Source: {_e(result.get('source_file'))} · {len(projects)} projects ·
      {_e(result.get('total_bidders', 0))} bid rows · {_e(result.get('flagged_projects', 0))} flagged ·
      Generated {_e(generated)}</p>
    <table>
      <tr><th>Project name</th><th>Location</th><th>First contractor</th><th class="num">Bid amount</th>
          <th>Lowest bidder</th><th>Bid date</th><th class="num">Bidders</th></tr>
      {_summary_rows(projects)}
    </table>
    """
    appendix_blocks = [
        "<h2>All bids by project</h2>"
        "<p class='meta'>Every bid row in listed order. The lowest priced bid in each project is in bold.</p>",
        *_bid_tables(projects),
    ]

    header_h = 62
    content = pymupdf.Rect(MARGIN, MARGIN + header_h, PAGE.width - MARGIN, PAGE.height - MARGIN - 18)
    def rectfn(_rect_num, _filled):
        return PAGE, content, None

    doc = pymupdf.Story(html=body, user_css=CSS).write_with_links(rectfn)
    appendix_doc = _layout_keep_together(appendix_blocks, content)
    doc.insert_pdf(appendix_doc)
    appendix_doc.close()

    logo = STATIC_DIR / "logo.png"
    logo_bytes = logo.read_bytes() if logo.exists() else None
    total = doc.page_count
    for page in doc:
        if logo_bytes:
            page.insert_image(pymupdf.Rect(MARGIN, MARGIN - 8, MARGIN + 165, MARGIN + 47), stream=logo_bytes)
        y_rule = MARGIN + header_h - 10
        page.draw_line((MARGIN, y_rule), (PAGE.width - MARGIN, y_rule), color=(0.655, 0.039, 0.039), width=1.5)
        page.insert_text((MARGIN, PAGE.height - MARGIN + 4), FOOTER_TEXT, fontsize=7, color=(0.37, 0.42, 0.48))
        label = f"Page {page.number + 1} of {total}"
        width = pymupdf.get_text_length(label, fontsize=7)
        page.insert_text((PAGE.width - MARGIN - width, PAGE.height - MARGIN + 4), label, fontsize=7,
                         color=(0.37, 0.42, 0.48))

    doc.set_metadata({"title": "PDF Bid Results", "author": "BIM Collaboration LLC",
                      "creator": "PDF Bid Results Extractor"})
    data = doc.tobytes(garbage=3, deflate=True)
    doc.close()
    return data
