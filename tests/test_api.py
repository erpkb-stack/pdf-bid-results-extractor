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
