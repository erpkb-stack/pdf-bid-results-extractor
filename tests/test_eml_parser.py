"""Tests for the Daily Email Report (.eml) parser.

Expected values come from the raw email HTML (counted independently of the
parser) and from the fields the user specified for the sample project.
"""
import re
from collections import Counter
from pathlib import Path

import pytest

from app.services.eml_parser import (
    EmailExtractionError,
    html_from_eml,
    parse_email_html,
    parse_eml_bytes,
    to_email_records,
)

SAMPLE = Path(__file__).parent / "fixtures" / "daily_report.eml"


@pytest.fixture(scope="module")
def parsed():
    projects, meta = parse_eml_bytes(SAMPLE.read_bytes())
    return to_email_records(projects), meta


@pytest.fixture(scope="module")
def records(parsed):
    return parsed[0]


def test_counts_match_raw_html(records):
    html, _ = html_from_eml(SAMPLE.read_bytes())
    assert len(records) == len(re.findall(r'<td colspan="3"><a href="[^"]*BidPackageID', html)) == 49
    assert Counter(r["category"] for r in records) == {
        "Results For: BIM - CA": 10,
        "New Projects Received": 4,
        "Bid Results Received within Last 5 Days": 29,
        "Projects with New Addenda": 6,
    }
    area_headers = len(re.findall(r"font-size:18px", html))
    assert len({(r["category"], r["area"]) for r in records if r["area"]}) == area_headers == 15
    assert sum(1 for r in records if r["bin_no"] is None) == 26
    assert not any(r["flags"] for r in records)


def test_user_specified_project(records):
    geo = [r for r in records if r["project_name"] == "IFB Geotechnical Exploration Drilling - Southern CA"]
    assert [g["category"] for g in geo] == ["New Projects Received", "Projects with New Addenda"]
    g = geo[0]
    assert g["area"] == "Los Angeles,Orange,Riverside,Various CA"
    assert g["bid_date"] == "11/05/2026"
    assert g["location"] == "San Diego And Various Other"
    assert g["bin_no"] == "Online"
    assert g["source"] == "Department of Transportation, Division of Procurement and Contracts - Staff"
    # The project number comes from THIS project's own link (AGCS26-04740 belongs to Tustin Legacy).
    assert g["project_no"] == "AGCS26-04773"
    assert g["project_url"].startswith("https://login.onlineplanservice.com/filter.aspx?")
    assert "BidPackageID=1378189" in g["project_url"]
    assert g["also_in"] == ["Projects with New Addenda"]


def test_sample_url_project_number(records):
    tustin = next(r for r in records if r["project_name"].startswith("Tustin Legacy Linear Park"))
    assert tustin["project_no"] == "AGCS26-04740"
    assert tustin["bid_package_id"] == "1377858"
    assert tustin["category"] == "Results For: BIM - CA"
    assert tustin["area"] is None  # the keyword-match section has no area headings


def test_project_numbers_match_bx_parts_of_url(records):
    """projectnum must equal bx + bxup (e.g. AGCS + 26-04773) for every project."""
    from urllib.parse import parse_qs, urlparse

    for r in records:
        q = parse_qs(urlparse(r["project_url"]).query)
        assert r["project_no"] == q["bx"][0] + q["bxup"][0], r["project_name"]


def test_area_resets_at_each_category(records):
    first_bid_result = next(r for r in records if r["category"] == "Bid Results Received within Last 5 Days")
    assert first_bid_result["area"] == "Butte CA"
    addenda = [r for r in records if r["category"] == "Projects with New Addenda"]
    assert [r["area"] for r in addenda] == [
        "Los Angeles,Orange,Riverside,Various CA", "Orange CA", "Riverside CA", "Riverside CA",
        "Stanislaus CA", "Stanislaus CA",
    ]


def test_header_and_footer_links_ignored(records):
    names = {r["project_name"] for r in records}
    for junk in ("Log Into Online Plans", "Unsubscribe", "Usage Agreement and Disclaimers", "info@cencalbx.com"):
        assert junk not in names


def test_metadata(parsed):
    _, meta = parsed
    assert meta["report_date"] == "11/01/2026"
    assert meta["subject"] == "Central California Builders Exchange Daily Email Report"


def test_missing_fields_are_flagged_not_invented():
    html = (
        '<strong style="font-size:20px">New Projects Received</strong>'
        '<table><tr><td colspan="3"><a href="https://login.onlineplanservice.com/filter.aspx?BidPackageID=1">'
        "Project Without Details</a></td></tr></table>"
    )
    (p,) = parse_email_html(html)
    assert p.project_no is None and p.bid_date is None and p.location is None and p.source is None
    assert set(p.flags) == {"missing_project_no", "missing_bid_date", "missing_location", "missing_source"}


def test_rejects_email_without_html():
    with pytest.raises(EmailExtractionError, match="no HTML body"):
        parse_eml_bytes(b"Subject: hi\r\n\r\njust text")


def test_rejects_unrelated_html_email():
    msg = b"Subject: hi\r\nContent-Type: text/html\r\n\r\n<p>Hello <a href='https://example.com'>there</a></p>"
    with pytest.raises(EmailExtractionError, match="No projects found"):
        parse_eml_bytes(msg)
