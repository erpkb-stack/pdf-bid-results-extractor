"""Fixed-column table renderer for PDF reports.

PyMuPDF's HTML table layout ignores percentage column widths and its
Story.write_with_links() can hang on long multi-table documents. This module
draws tables itself instead:

  * every cell is an HTML box of a fixed width (exact column widths);
  * row height = tallest cell, measured before drawing;
  * a row never splits across pages; column headings repeat on each new page;
  * links are added from the known cell rectangle (one link per cell).
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import pymupdf

NAVY = (0.090, 0.039, 0.573)       # #170a92
NAVY_SOFT = (0.933, 0.929, 0.980)  # #eeedfa
RULE = (0.875, 0.890, 0.910)       # #dfe3e8
PAD_X, PAD_Y = 4.0, 3.0


@dataclass
class Column:
    label: str
    width: float            # fraction of the table width; fractions should sum to 1
    align: str = "left"     # "left" | "right"


@dataclass
class Row:
    cells: list[str]                 # HTML per cell (already escaped by the caller)
    link: str | None = None          # URI for the first cell
    band: str | None = None          # optional group band drawn above this row (e.g. an area)


@dataclass
class Section:
    columns: list[Column]
    rows: list[Row]
    title: str | None = None         # HTML heading drawn above the table
    continued_label: str | None = None  # plain text shown on continuation pages
    gap_after: float = 14


class _PagePlan:
    """Records what goes on one page. Rendered later in one pass (see Layout.render)."""

    def __init__(self) -> None:
        self.html: list[tuple[pymupdf.Rect, str]] = []
        self.rects: list[tuple[pymupdf.Rect, tuple]] = []
        self.lines: list[tuple[tuple, tuple, tuple, float]] = []
        self.links: list[tuple[pymupdf.Rect, str]] = []

    def draw_rect(self, rect, color=None, fill=None) -> None:
        self.rects.append((pymupdf.Rect(rect), fill))

    def draw_line(self, p1, p2, color=(0, 0, 0), width=1.0) -> None:
        self.lines.append((p1, p2, color, width))

    def insert_link(self, link: dict) -> None:
        self.links.append((pymupdf.Rect(link["from"]), link["uri"]))


@dataclass
class Layout:
    """Two-pass table layout: plan pages (measure + position), then render once.

    Rendering draws each HTML box with Story.place/draw on a DocumentWriter page,
    which is ~40x faster than page.insert_htmlbox() per cell. Backgrounds are then
    added *behind* the text, followed by rules and links.
    """
    page: pymupdf.Rect
    content: pymupdf.Rect
    css: str
    y: float = 0.0
    _pages: list = field(default_factory=list)
    _measure_cache: dict = field(default_factory=dict)

    # ---- primitives -------------------------------------------------------
    def new_page(self) -> None:
        self._pages.append(_PagePlan())
        self.y = self.content.y0

    @property
    def current(self) -> _PagePlan:
        if not self._pages:
            self.new_page()
        return self._pages[-1]

    def render(self) -> pymupdf.Document:
        buffer = io.BytesIO()
        writer = pymupdf.DocumentWriter(buffer)
        for plan in self._pages:
            device = writer.begin_page(self.page)
            for rect, html in plan.html:
                story = pymupdf.Story(html=html, user_css=self.css)
                story.place(rect)
                story.draw(device)
            writer.end_page()
        writer.close()

        doc = pymupdf.open("pdf", buffer.getvalue())
        for page, plan in zip(doc, self._pages):
            for rect, fill in plan.rects:
                page.draw_rect(rect, color=None, fill=fill, overlay=False)  # behind the text
            for p1, p2, color, width in plan.lines:
                page.draw_line(p1, p2, color=color, width=width)
            for rect, uri in plan.links:
                page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": uri})
        return doc

    def measure(self, html: str, width: float) -> float:
        key = (html, round(width, 2))
        cached = self._measure_cache.get(key)
        if cached is None:
            story = pymupdf.Story(html=html, user_css=self.css)
            _, filled = story.place(pymupdf.Rect(0, 0, width, 10_000))
            cached = self._measure_cache[key] = pymupdf.Rect(filled).height
        return cached

    def html_box(self, rect: pymupdf.Rect, html: str) -> None:
        # Give the box generous height: rows are pre-measured, so nothing is cut off.
        self.current.html.append((pymupdf.Rect(rect.x0, rect.y0, rect.x1, rect.y1 + 20), html))

    def space_left(self) -> float:
        return self.content.y1 - self.y

    # ---- blocks -----------------------------------------------------------
    def paragraph(self, html: str, gap: float = 4) -> None:
        width = self.content.width
        h = self.measure(html, width)
        if h > self.space_left() and self.y > self.content.y0:
            self.new_page()
        self.html_box(pymupdf.Rect(self.content.x0, self.y, self.content.x1, self.y + h + 1), html)
        self.y += h + gap

    def _col_rects(self, columns: list[Column], top: float, height: float) -> list[pymupdf.Rect]:
        x = self.content.x0
        rects = []
        for c in columns:
            w = self.content.width * c.width
            rects.append(pymupdf.Rect(x, top, x + w, top + height))
            x += w
        return rects

    def _cell_html(self, html: str, align: str, cls: str = "") -> str:
        style = "text-align:right;" if align == "right" else ""
        return f"<div class='cell {cls}' style='{style}'>{html}</div>"

    def _header_height(self, columns: list[Column]) -> float:
        return max(self.measure(self._cell_html(c.label, c.align, "th"), self.content.width * c.width - 2 * PAD_X)
                   for c in columns) + 2 * PAD_Y

    def header_row(self, columns: list[Column]) -> None:
        h = self._header_height(columns)
        page = self.current
        page.draw_rect(pymupdf.Rect(self.content.x0, self.y, self.content.x1, self.y + h), color=None, fill=NAVY)
        for c, r in zip(columns, self._col_rects(columns, self.y, h)):
            inner = pymupdf.Rect(r.x0 + PAD_X, r.y0 + PAD_Y, r.x1 - PAD_X, r.y1)
            self.html_box(inner, self._cell_html(c.label, c.align, "th"))
        self.y += h

    def band_height(self, html: str) -> float:
        return self.measure(f"<div class='band'>{html}</div>", self.content.width - 2 * PAD_X) + 2 * PAD_Y

    def band(self, html: str) -> float:
        h = self.band_height(html)
        page = self.current
        page.draw_rect(pymupdf.Rect(self.content.x0, self.y, self.content.x1, self.y + h), color=None, fill=NAVY_SOFT)
        self.html_box(pymupdf.Rect(self.content.x0 + PAD_X, self.y + PAD_Y, self.content.x1 - PAD_X, self.y + h),
                      f"<div class='band'>{html}</div>")
        self.y += h
        return h

    def section(self, s: Section) -> None:
        header_h = self._header_height(s.columns)
        first_row_h = self._row_height(s.columns, s.rows[0]) if s.rows else 0
        title_h = self.measure(s.title, self.content.width) + 4 if s.title else 0
        band_h = self.band_height(s.rows[0].band) if s.rows and s.rows[0].band else 0
        # Keep the title, the column headings and the first row together.
        if title_h + header_h + band_h + first_row_h > self.space_left() and self.y > self.content.y0:
            self.new_page()
        if s.title:
            self.paragraph(s.title, gap=4)
        self.header_row(s.columns)

        current_band: str | None = None
        for row in s.rows:
            row_h = self._row_height(s.columns, row)
            band = row.band
            needed = row_h + (self.band_height(band) if band else 0)
            if needed > self.space_left():
                self.new_page()
                if s.continued_label:
                    self.paragraph(f"<p class='cont'>{s.continued_label} (continued)</p>", gap=2)
                self.header_row(s.columns)
                band = band or current_band  # repeat the group band on the new page
            if band:
                self.band(band)
                current_band = band
            self._draw_row(s.columns, row, row_h)
        self.y += s.gap_after

    def _row_height(self, columns: list[Column], row: Row) -> float:
        return max(
            self.measure(self._cell_html(html, c.align), self.content.width * c.width - 2 * PAD_X)
            for c, html in zip(columns, row.cells)
        ) + 2 * PAD_Y

    def _draw_row(self, columns: list[Column], row: Row, h: float) -> None:
        page = self.current
        rects = self._col_rects(columns, self.y, h)
        for c, r, html in zip(columns, rects, row.cells):
            inner = pymupdf.Rect(r.x0 + PAD_X, r.y0 + PAD_Y, r.x1 - PAD_X, r.y1 + 1)
            self.html_box(inner, self._cell_html(html, c.align))
        if row.link:
            first = rects[0]
            page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(first.x0, first.y0, first.x1, first.y1),
                              "uri": row.link})
        page.draw_line((self.content.x0, self.y + h), (self.content.x1, self.y + h), color=RULE, width=0.5)
        self.y += h


TABLE_CSS = """
* { font-family: sans-serif; }
body { margin: 0; padding: 0; }
div.cell { font-size: 8.5pt; color: #1b1f24; }
div.th { font-size: 7.5pt; color: #ffffff; font-weight: bold; }
div.band { font-size: 8.5pt; color: #170a92; font-weight: bold; text-align: right; }
a, span.link { color: #170a92; font-weight: bold; text-decoration: underline; }
span.flag { color: #9a5b00; font-size: 7pt; font-weight: bold; }
span.diff { color: #9a5b00; font-weight: bold; }
span.muted { color: #5f6b7a; }
h1 { font-size: 15pt; color: #170a92; margin: 0; }
h2 { font-size: 11pt; color: #170a92; margin: 0; }
span.count { color: #5f6b7a; font-weight: normal; }
p.meta { font-size: 8.5pt; color: #5f6b7a; margin: 0; }
p.cont { font-size: 8.5pt; color: #170a92; font-weight: bold; margin: 0; }
"""
