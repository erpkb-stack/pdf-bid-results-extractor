"""Parser tests against the sample Bid Results PDF.

EXPECTED is a hand-verified answer key built from the PDF's raw text coordinates
(not from the parser's own output). If onlineplanservice.com changes its
template, these tests are what should fail first.
"""
from decimal import Decimal
from pathlib import Path

import pymupdf
import pytest

from app.services.parser import (
    ExtractionError,
    _is_amount,
    _read_lines,
    parse_pdf_bytes,
    to_records,
)

SAMPLE = Path(__file__).parent / "fixtures" / "input.pdf"
URL_PREFIX = "https://login.onlineplanservice.com/filter.aspx?BidPackageID="

# (date, name, location, first contractor, first amount, lowest contractor, lowest amount, bidders, BidPackageID)
EXPECTED = [
    ("8/27/2026", "Rancho Tract Water Main Replacement", "California City",
     "Kirk Construction", "891523.40", "Kirk Construction", "891523.40", 8, "1362931"),
    ("9/1/2026", "Bystrum Area - Phase 1 Strengthen & Replace Water Mains", "Modesto",
     "Mid Cal Pipeline & Utilities, Inc.", "3863122.50", "Mid Cal Pipeline & Utilities, Inc.", "3863122.50", 12, "1362799"),
    ("9/1/2026", "Door Repair & Door Operator Replacement, Stockton Combined Support Maintenance Shop (CSMS)",
     "Stockton", "Nor-Cal Overhead, Inc", "51084.18", "Nor-Cal Overhead, Inc", "51084.18", 1, "1365090"),
    ("9/2/2026", "East Las Palmas Avenue Over San Joaquin River Bridge Revegetation Project", "Patterson",
     "Arboricultural Specialties", "117000.00", "Arboricultural Specialties", "117000.00", 2, "1363164"),
    ("9/2/2026", "FY 27 Sewer Manhole Repair and Recoating Project", "Hanford",
     "Sancon Technologies", "58839.00", "Sancon Technologies", "58839.00", 2, "1364139"),
    ("9/2/2026", "SSJID Restroom Accessibility Upgrade Building B", "Manteca",
     "Sierra Build Group", "176172.00", "Sierra Build Group", "176172.00", 4, "1364266"),
    ("9/2/2026", "Construction of Residential Improvements in Drainage Area DK", "Fresno",
     "GC Builders", "122375.00", "GC Builders", "122375.00", 2, "1366329"),
    ("9/3/2026", "Sitework Bid Package-New Restroom Modular Building at Pixley Elementary School", "Pixley",
     "Oral E. Micham, Inc.", "774000.00", "Oral E. Micham, Inc.", "774000.00", 6, "1363755"),
    ("9/3/2026", "Avenue 176 at Road 276 Box Culvert Replacement Project", "Porterville",
     "Archer Civil Construction LLC", "498480.57", "Archer Civil Construction LLC", "498480.57", 7, "1364792"),
    ("9/3/2026", "Signal Coordination on Lander Ave", "Turlock",
     "Tim Paxin's Pacific Excavation, Inc.", "119248.00", "St. Francis Electric", "60505.00", 4, "1365034"),
    ("9/3/2026", "Signal Coordination on Christoffersen Parkway", "Turlock",
     "DC Electric Group", "169955.00", "DC Electric Group", "169955.00", 4, "1365047"),
    ("9/3/2026", "TUSD Kimball High School 2-Story CR Building Increment 1", "Tracy",
     "F & H Construction", "5237000.00", "F & H Construction", "5237000.00", 4, "1366090"),
    ("9/3/2026", "SB1383 2026 Procurement Requirement for Recovered Organic Waste Product", "Fresno",
     "Agromin", None, None, None, 5, "1367708"),
    ("9/4/2026", "FY2026/2027 Asphalt/Concrete Patching On-Call Services", "Madera",
     "Tosted Asphalt Incorporated", "245550.00", "Tosted Asphalt Incorporated", "245550.00", 7, "1364142"),
    ("9/8/2026", "Road Rehabilitation Various Locations", "Madera",
     "Avison Construction, Inc.", "891450.00", "Avison Construction, Inc.", "891450.00", 10, "1365577"),
]


@pytest.fixture(scope="module")
def pdf_bytes() -> bytes:
    return SAMPLE.read_bytes()


@pytest.fixture(scope="module")
def records(pdf_bytes):
    return to_records(parse_pdf_bytes(pdf_bytes))


def _dec(v):
    return None if v is None else Decimal(str(v)).quantize(Decimal("0.01"))


def test_project_count(records):
    assert len(records) == 15


@pytest.mark.parametrize("idx", range(len(EXPECTED)))
def test_project_matches_answer_key(records, idx):
    date, name, loc, first, first_amt, low, low_amt, n, pkg = EXPECTED[idx]
    r = records[idx]
    assert r["project_date"] == date
    assert r["project_name"] == name
    assert r["location"] == loc
    assert r["first_contractor"] == first
    assert _dec(r["bid_amount"]) == _dec(first_amt)
    assert r["lowest_contractor"] == low
    assert _dec(r["lowest_amount"]) == _dec(low_amt)
    assert r["bidder_count"] == n
    assert r["project_url"].startswith(URL_PREFIX + pkg + "&")


def test_report_date_is_not_a_bid_date(records):
    # 9/10/2026 is the report date in the page-1 header, never a bid date.
    assert all(r["project_date"] != "9/10/2026" for r in records)


def test_project_crossing_page_break_keeps_its_bidder(records):
    door = records[2]
    assert door["bidders"] == [
        {"contractor_name": "Nor-Cal Overhead, Inc", "bid_amount": 51084.18, "amount_status": "ok"}
    ]
    road = records[-1]
    assert [b["contractor_name"] for b in road["bidders"][-2:]] == ["Dawson-Mauldin LLC", "Katch Environmental, Inc"]


def test_subcontractors_are_excluded(records):
    names = {b["contractor_name"] for r in records for b in r["bidders"]}
    for sub in ("Dunns", "Rescom", "CMC Rebar", "Magnetar", "Fence"):
        assert not any(sub in n for n in names)
    assert len(records[7]["bidders"]) == 6   # Pixley sitework, has a SUBCONTRACTORS block
    assert len(records[8]["bidders"]) == 7   # Avenue 176, has a SUBCONTRACTORS block


def test_zero_prices_are_null_and_flagged(records):
    sb = records[12]
    assert all(b["bid_amount"] is None and b["amount_status"] == "no_price" for b in sb["bidders"])
    assert "no_prices_listed" in sb["flags"]


def test_first_not_lowest_is_flagged_only_where_true(records):
    flagged = [r["project_name"] for r in records if "first_listed_not_lowest" in r["flags"]]
    assert flagged == ["Signal Coordination on Lander Ave"]


def test_every_project_has_a_distinct_url(records):
    urls = [r["project_url"] for r in records]
    assert None not in urls and len(set(urls)) == 15


def test_every_amount_in_pdf_is_used_once(pdf_bytes, records):
    """Reconciliation: the parser must neither drop nor duplicate an amount."""
    lines = _read_lines(pymupdf.open(stream=pdf_bytes, filetype="pdf"))
    amount_lines = [l for l in lines if _is_amount(l)]
    bidders = [b for r in records for b in r["bidders"]]
    assert len(bidders) == len(amount_lines) == 78
    pdf_total = sum(Decimal(l.text.replace("$", "").replace(",", "")) for l in amount_lines)
    out_total = sum(_dec(b["bid_amount"]) for b in bidders if b["bid_amount"] is not None)
    assert out_total == pdf_total == Decimal("119534052.39")


def test_special_characters_preserved(records):
    names = {b["contractor_name"] for r in records for b in r["bidders"]}
    assert "Tim Paxin's Excavation" in names
    assert "CLS/Cervantes Landscape Services, Inc." in names
    assert "Sierra Costruction & Excavation" in names  # source typo kept as printed


def test_rejects_non_pdf():
    with pytest.raises(ExtractionError):
        parse_pdf_bytes(b"not a pdf at all")


def test_rejects_text_less_pdf():
    doc = pymupdf.open()
    doc.new_page()
    with pytest.raises(ExtractionError, match="OCR"):
        parse_pdf_bytes(doc.tobytes())
