"""Parser for onlineplanservice.com "Daily Email Report" .eml files.

Email layout (verified against a Central California Builders Exchange sample):
  * Category heading:  <strong style="... font-size:20px ...">New Projects Received</strong>
    The first section is "Results For: <keyword list>" (keyword matches).
  * Area heading:      <th style="text-align:right"><strong style="... font-size:18px ...">Orange CA</strong>
    Optional; the "Results For" section has none.
  * Project:           a small table with three rows:
        <td colspan="3"><a href="...filter.aspx?...&projectnum=AGCS26-04773...">Project name</a></td>
        <td>Bid Date:11/05/2026</td><td>Location: San Diego ...</td><td>Bin #: Online</td>
        <td colspan="3">Source: Department of Transportation ...</td>

Only Python's standard library is used. The parser never invents values: anything
it cannot read is null and the record is flagged.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from email import message_from_bytes, policy
from html import unescape
from html.parser import HTMLParser
from urllib.parse import parse_qs, urlparse

CATEGORY_SIZE = re.compile(r"font-size\s*:\s*20px", re.I)
AREA_SIZE = re.compile(r"font-size\s*:\s*18px", re.I)
WS = re.compile(r"\s+")


class EmailExtractionError(Exception):
    """Raised when the file is not a readable onlineplanservice daily email."""


def _clean(text: str) -> str:
    return WS.sub(" ", unescape(text)).strip()


@dataclass
class EmailProject:
    category: str | None
    area: str | None
    project_name: str
    project_url: str | None
    project_no: str | None = None
    bid_package_id: str | None = None
    bid_date: str | None = None
    location: str | None = None
    bin_no: str | None = None
    source: str | None = None
    also_in: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


class _Tokenizer(HTMLParser):
    """Turn the email HTML into a flat stream of (kind, value) tokens.

    kinds: "category" | "area" | "link" (href, text) | "cell" (text of a <td> outside links)
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tokens: list[tuple] = []
        self._strong_kind: str | None = None
        self._strong_text: list[str] = []
        self._in_td = False
        self._td_text: list[str] = []
        self._link_href: str | None = None
        self._link_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "strong":
            style = a.get("style") or ""
            if CATEGORY_SIZE.search(style):
                self._strong_kind = "category"
            elif AREA_SIZE.search(style):
                self._strong_kind = "area"
            else:
                self._strong_kind = None
            self._strong_text = []
        elif tag == "td":
            self._in_td = True
            self._td_text = []
        elif tag == "a":
            self._link_href = a.get("href")
            self._link_text = []

    def handle_endtag(self, tag):
        if tag == "strong" and self._strong_kind:
            text = _clean("".join(self._strong_text))
            if text:
                self.tokens.append((self._strong_kind, text))
            self._strong_kind = None
        elif tag == "a" and self._link_href is not None:
            self.tokens.append(("link", self._link_href, _clean("".join(self._link_text))))
            self._link_href = None
        elif tag == "td" and self._in_td:
            text = _clean("".join(self._td_text))
            if text:
                self.tokens.append(("cell", text))
            self._in_td = False

    def handle_data(self, data):
        if self._strong_kind:
            self._strong_text.append(data)
        if self._link_href is not None:
            self._link_text.append(data)
        elif self._in_td and not self._strong_kind:
            self._td_text.append(data)


def _is_project_link(href: str | None) -> bool:
    if not href:
        return False
    parsed = urlparse(href)
    return parsed.scheme in ("http", "https") and "BidPackageID" in (parsed.query or "")


def _query_value(url: str, key: str) -> str | None:
    values = parse_qs(urlparse(url).query).get(key)
    return values[0].strip() if values and values[0].strip() else None


def _after_label(text: str, label: str) -> str | None:
    """'Bid Date:11/05/2026' -> '11/05/2026'; empty value -> None."""
    value = text[len(label):].strip() if text.lower().startswith(label.lower()) else None
    return value or None


def html_from_eml(data: bytes) -> tuple[str, dict]:
    try:
        msg = message_from_bytes(data, policy=policy.default)
    except Exception as exc:  # noqa: BLE001
        raise EmailExtractionError("The file could not be read as an email (.eml).") from exc

    body = msg.get_body(preferencelist=("html",))
    if body is None:
        raise EmailExtractionError("The email has no HTML body, so there are no project links to read.")
    try:
        html = body.get_content()
    except Exception as exc:  # noqa: BLE001
        raise EmailExtractionError("The email's HTML body could not be decoded.") from exc

    meta = {
        "subject": _clean(str(msg.get("subject", ""))) or None,
        "sender": _clean(str(msg.get("from", ""))) or None,
        "sent": _clean(str(msg.get("date", ""))) or None,
    }
    return html, meta


def parse_email_html(html: str) -> list[EmailProject]:
    tok = _Tokenizer()
    tok.feed(html)
    tok.close()

    projects: list[EmailProject] = []
    category: str | None = None
    area: str | None = None
    current: EmailProject | None = None

    for t in tok.tokens:
        kind = t[0]
        if kind == "category":
            category, area, current = t[1], None, None
        elif kind == "area":
            area, current = t[1], None
        elif kind == "link":
            href, text = t[1], t[2]
            if not _is_project_link(href) or category is None:
                continue  # header/footer links (login, unsubscribe, legal)
            current = EmailProject(
                category=category,
                area=area,
                project_name=text or "(untitled project)",
                project_url=href,
                project_no=_query_value(href, "projectnum"),
                bid_package_id=_query_value(href, "BidPackageID"),
            )
            projects.append(current)
        elif kind == "cell" and current is not None:
            text = t[1]
            if text.lower().startswith("bid date:"):
                current.bid_date = _after_label(text, "Bid Date:")
            elif text.lower().startswith("location:"):
                current.location = _after_label(text, "Location:")
            elif text.lower().startswith("bin #:"):
                current.bin_no = _after_label(text, "Bin #:")
            elif text.lower().startswith("source:"):
                current.source = _after_label(text, "Source:")
                current = None  # Source is the last row of a project block

    _cross_reference(projects)
    _validate(projects)
    return projects


def _cross_reference(projects: list[EmailProject]) -> None:
    """Note other categories the same project appears in (e.g. New Projects and New Addenda)."""
    by_key: dict[str, list[EmailProject]] = {}
    for p in projects:
        key = p.bid_package_id or p.project_url or p.project_name
        by_key.setdefault(key, []).append(p)
    for group in by_key.values():
        cats = [p.category for p in group]
        for p in group:
            p.also_in = sorted({c for c in cats if c and c != p.category})


def _validate(projects: list[EmailProject]) -> None:
    for p in projects:
        if not p.project_no:
            p.flags.append("missing_project_no")
        if not p.bid_date:
            p.flags.append("missing_bid_date")
        if not p.location:
            p.flags.append("missing_location")
        if not p.source:
            p.flags.append("missing_source")


def parse_eml_bytes(data: bytes) -> tuple[list[EmailProject], dict]:
    html, meta = html_from_eml(data)
    projects = parse_email_html(html)
    if not projects:
        raise EmailExtractionError(
            "No projects found. This email does not match the onlineplanservice.com "
            "Daily Email Report layout."
        )
    meta["report_date"] = _report_date(html)
    return projects, meta


def _report_date(html: str) -> str | None:
    m = re.search(r"Daily Project Email for .*? on (\d{1,2}/\d{1,2}/\d{4})", _clean(re.sub(r"<[^>]+>", " ", html)))
    return m.group(1) if m else None


def to_email_records(projects: list[EmailProject]) -> list[dict]:
    return [
        {
            "category": p.category,
            "area": p.area,
            "project_name": p.project_name,
            "project_url": p.project_url,
            "project_no": p.project_no,
            "bid_package_id": p.bid_package_id,
            "bid_date": p.bid_date,
            "location": p.location,
            "bin_no": p.bin_no,
            "source": p.source,
            "also_in": p.also_in,
            "flags": p.flags,
        }
        for p in projects
    ]
