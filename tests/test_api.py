from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)
SAMPLE = Path(__file__).parent / "fixtures" / "input.pdf"


def test_health():
    assert client.get("/api/health").json() == {"status": "ok"}


def test_index_served():
    res = client.get("/")
    assert res.status_code == 200 and "PDF Bid Results Extractor" in res.text
    for text in ("/static/logo.png", "tel:801-792-8999", "mailto:contact@bimcollaboration.net",
                 "BIM Collaboration LLC, 2021 - 2026"):
        assert text in res.text


def test_logo_served():
    res = client.get("/static/logo.png")
    assert res.status_code == 200 and res.headers["content-type"] == "image/png"


def test_extract_sample():
    with SAMPLE.open("rb") as f:
        res = client.post("/api/extract", files={"file": ("input.pdf", f, "application/pdf")})
    assert res.status_code == 200
    body = res.json()
    assert body["source_file"] == "input.pdf"
    assert body["total_projects"] == 15
    assert body["total_bidders"] == 78
    assert body["flagged_projects"] == 2


def test_client_path_is_not_echoed():
    with SAMPLE.open("rb") as f:
        res = client.post("/api/extract", files={"file": ("/Users/me/secret/input.pdf", f, "application/pdf")})
    assert res.json()["source_file"] == "input.pdf"


def test_rejects_wrong_extension():
    res = client.post("/api/extract", files={"file": ("a.txt", b"%PDF-1.7", "text/plain")})
    assert res.status_code == 400


def test_rejects_fake_pdf():
    res = client.post("/api/extract", files={"file": ("a.pdf", b"hello", "application/pdf")})
    assert res.status_code == 400


def test_rejects_oversize():
    big = b"%PDF-" + b"0" * (20 * 1024 * 1024)
    res = client.post("/api/extract", files={"file": ("big.pdf", big, "application/pdf")})
    assert res.status_code == 413


def _extracted():
    with SAMPLE.open("rb") as f:
        return client.post("/api/extract", files={"file": ("input.pdf", f, "application/pdf")}).json()


def test_export_pdf_report():
    import pymupdf

    res = client.post("/api/export/pdf", json=_extracted())
    assert res.status_code == 200
    assert res.headers["content-type"] == "application/pdf"
    assert 'filename="input-report.pdf"' in res.headers["content-disposition"]
    doc = pymupdf.open(stream=res.content, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    assert "Rancho Tract Water Main Replacement" in text
    assert "BIM Collaboration LLC" in text
    assert [line.strip() for line in text.splitlines()].count("no price") == 5
    uris = {l["uri"] for page in doc for l in page.get_links() if l.get("uri")}
    assert len(uris) == 15  # every project title is a working link


def test_export_pdf_rejects_bad_payload():
    assert client.post("/api/export/pdf", json={"projects": "nope"}).status_code == 422
    assert client.post("/api/export/pdf", content=b"x" * (5 * 1024 * 1024 + 1)).status_code == 413


def test_export_pdf_escapes_html_and_drops_unsafe_links():
    import pymupdf

    payload = {"projects": [{"project_name": "<b>Evil</b> & Co", "project_url": "javascript:alert(1)",
                             "bidders": [{"contractor_name": "<script>x</script>", "bid_amount": 10}]}]}
    res = client.post("/api/export/pdf", json=payload)
    assert res.status_code == 200
    doc = pymupdf.open(stream=res.content, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    assert "<b>Evil</b> & Co" in text and "<script>x</script>" in text  # shown literally, not interpreted
    assert not [l for page in doc for l in page.get_links() if l.get("uri")]


# --------------------------- Daily Email Report (.eml) ---------------------------
EML = Path(__file__).parent / "fixtures" / "daily_report.eml"


def _eml_extracted():
    with EML.open("rb") as f:
        return client.post("/api/extract-eml", files={"file": ("daily_report.eml", f, "message/rfc822")}).json()


def test_extract_eml():
    body = _eml_extracted()
    assert body["total_projects"] == 49
    assert body["unique_projects"] == 48
    assert body["report_date"] == "11/01/2026"
    assert [c["count"] for c in body["categories"]] == [10, 4, 29, 6]


def test_extract_eml_rejects_wrong_files():
    assert client.post("/api/extract-eml", files={"file": ("a.pdf", b"%PDF-1.7", "application/pdf")}).status_code == 400
    assert client.post("/api/extract-eml", files={"file": ("a.eml", b"", "message/rfc822")}).status_code == 400
    assert client.post("/api/extract-eml", files={"file": ("a.eml", b"Subject: x\r\n\r\nhi", "message/rfc822")}).status_code == 422
    big = b"x" * (20 * 1024 * 1024 + 1)
    assert client.post("/api/extract-eml", files={"file": ("a.eml", big, "message/rfc822")}).status_code == 413


def test_export_eml_pdf():
    import pymupdf

    body = _eml_extracted()
    res = client.post("/api/export/eml-pdf", json=body)
    assert res.status_code == 200 and res.headers["content-type"] == "application/pdf"
    assert 'filename="daily_report-report.pdf"' in res.headers["content-disposition"]
    doc = pymupdf.open(stream=res.content, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    for expected in ("Daily Email Report", "New Projects Received", "AGCS26-04773", "San Diego And Various",
                     "Projects with New Addenda", "BIM Collaboration LLC"):
        assert expected in text
    links = [l["uri"] for page in doc for l in page.get_links() if l.get("uri")]
    assert len(links) == 49 and len(set(links)) == 48  # one link per row; one project appears twice


def test_export_eml_pdf_filtered_subset():
    import pymupdf

    body = _eml_extracted()
    subset = [p for p in body["projects"] if p["category"] == "New Projects Received"]
    res = client.post("/api/export/eml-pdf", json={"source_file": "x.eml", "projects": subset})
    doc = pymupdf.open(stream=res.content, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    assert "4 projects in 1 categories" in text
    assert "Bid Results Received" not in text


def test_export_eml_pdf_escapes_and_drops_unsafe_links():
    import pymupdf

    payload = {"projects": [{"project_name": "<i>x</i> & y", "project_url": "javascript:alert(1)",
                             "source": "<script>z</script>", "category": "Cat"}]}
    res = client.post("/api/export/eml-pdf", json=payload)
    assert res.status_code == 200
    doc = pymupdf.open(stream=res.content, filetype="pdf")
    text = "".join(page.get_text() for page in doc)
    assert "<i>x</i> & y" in text and "<script>z</script>" in text
    assert not [l for page in doc for l in page.get_links() if l.get("uri")]


def test_index_has_both_tabs():
    html = client.get("/").text
    assert 'id="tab-pdf"' in html and 'id="tab-eml"' in html
    for asset in ("/static/eml.js", "/static/tabs.js"):
        assert client.get(asset).status_code == 200


def test_large_reports_build_quickly():
    """Regression guard: Story.write_with_links() used to hang on long multi-table reports."""
    import time

    import pymupdf

    from app.services.pdf_report import build_email_report_pdf

    projects = [
        {"category": f"Category {i % 4}", "area": f"Area {i % 9} CA", "project_name": f"Project number {i} " * 3,
         "project_url": f"https://login.onlineplanservice.com/filter.aspx?BidPackageID={i}&projectnum=AGCS26-{i:05d}",
         "project_no": f"AGCS26-{i:05d}", "bid_date": "11/05/2026", "location": "Somewhere",
         "bin_no": "Online", "source": "Some Agency - Purchasing Department - Staff"}
        for i in range(300)
    ]
    projects.sort(key=lambda p: (p["category"], p["area"]))
    start = time.time()
    pdf = build_email_report_pdf({"source_file": "big.eml", "projects": projects})
    assert time.time() - start < 15
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    assert sum(1 for page in doc for l in page.get_links() if l.get("uri")) == 300
