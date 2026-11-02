# PDF Bid Results Extractor

A two-tab web app for onlineplanservice.com bid data:

- **PDF Bid Results** tab: upload a **Bid Results** PDF and get every project with its date, location, embedded link, first-listed contractor, lowest bidder and the full bid list.
- **Daily Email Report (.eml)** tab: upload a saved **Daily Email Report** email and get every project with its category, area, name, link, project number, bid date, location, Bin # and source.

Both tabs can export JSON, CSV and a branded PDF report, and both print directly.

Stack: Python 3.12+, FastAPI, PyMuPDF, and a vanilla HTML/CSS/JS front end. It needs no database and no LLM, and uploaded files are never written to disk.

## Run on macOS

```bash
cd pdf-bid-results-extractor
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Then open http://127.0.0.1:8000.

> If `python3 --version` reports a version older than 3.12, install a newer one with `brew install python@3.12` and use `python3.12 -m venv .venv`.

### Troubleshooting: `pip install` fails while building PyMuPDF

If pip prints `Preparing metadata (pyproject.toml) ... error` and then a long MuPDF build log, it found no prebuilt wheel and fell back to compiling from source. This usually happens with **Anaconda's Python on Intel Macs**: it reports the OS as `macOS-10.16`, and newer PyMuPDF releases don't publish wheels for that tag. `requirements.txt` therefore pins `pymupdf<1.28`. If the error still appears:

```bash
rm -rf .venv
python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip
pip install --only-binary=:all: -r requirements.txt   # wheels only; never compile
```

As an alternative, create the venv with a non-Anaconda Python (`brew install python@3.12`, then `/usr/local/bin/python3.12 -m venv .venv`).

Run the tests:

```bash
pytest -q
```

The 54 tests include:

- A hand-verified answer key covering all 15 projects in `tests/fixtures/input.pdf`. The tests also reconcile every bid amount in the PDF against the output, which must total exactly $119,534,052.39 across 78 rows.
- Counts taken straight from the raw HTML of `tests/fixtures/daily_report.eml`: 49 projects in 4 categories, 15 area headings, and 26 projects with no Bin #.
- A check that every project number equals the `bx` + `bxup` parts of its own URL.
- A speed guard confirming that a 300-project PDF report builds in seconds.

## Decisions baked into v1

| Question | Default chosen | Where to change it |
| --- | --- | --- |
| "First contractor" means… | Both values are reported: `first_contractor` (first row listed) and `lowest_contractor` (lowest priced bid). The project is flagged `first_listed_not_lowest` when they differ. In the sample this happens once, on Lander Ave. | `to_records()` in `app/services/parser.py` |
| A literal `0` amount | Treated as **no price**, not a $0 bid: `bid_amount: null`, `amount_status: "no_price"`, and the project is flagged `no_prices_listed`. Example: SB1383. | `_parse_amount()` |
| Scope | **This template only** (onlineplanservice.com Bid Results). Other layouts are rejected with a clear error rather than parsed badly. | Module docstring of `parser.py` |

## How the parser works

The PDF's internal text order is scrambled, and projects continue across page breaks. Because of that, the parser reads every text line with its coordinates and processes the whole document as a single stream sorted by (page, y, x):

| Element | Rule |
| --- | --- |
| Project title | Blue (#0000FF) text. A second blue line within 20pt is treated as a wrapped title. |
| Bid date | Bold `m/d/yyyy` text. The non-bold report date in the page-1 header is ignored. |
| Location | Bold text with x > 400, matched to the **nearest title by y-distance**. Locations on wrapped titles sit between the title lines, so reading them in sequence would attach them to the wrong project. |
| Contractor | Regular 11pt text at x < 60. Each contractor belongs to the latest title above it, including when that title is on the previous page. |
| Bid amount | Money text at x > 280 on the same row as the contractor (within 3pt). |
| Subcontractors | Anything under 10pt is ignored. |
| Link | The URI link rectangle overlapping the title. Zero-width link artifacts are dropped. |

### Validation flags

A flag means a person should look at that row. The parser never fills in a value it didn't read.

| Flag | Meaning |
| --- | --- |
| `first_listed_not_lowest` | The first row is not the lowest bid |
| `no_prices_listed` | Every bidder shows 0 or no price |
| `no_bidders` | A title was found with no bidder rows under it |
| `bidder_missing_amount` | A contractor row has no amount on its line |
| `missing_location` / `missing_date` / `missing_url` | That field could not be found |

If a newly uploaded PDF suddenly produces many flags, onlineplanservice.com has probably changed its template. In that case, update the rules above and add the new file to `tests/fixtures`.

## Daily Email Report (.eml) tab

**Saving the email as .eml**

- **Apple Mail:** select the email, choose File › Save As, and set Format to *Raw Message Source*.
- **Outlook for Mac:** drag the email onto the Desktop or into a Finder folder.
- **Gmail web:** open the email and choose ⋮ › *Download message*.

**What is extracted per project**

| Field | Where it comes from |
| --- | --- |
| Category | The 20px bold heading, e.g. *New Projects Received*. The first section, *Results For: <your keywords>*, lists keyword matches. |
| Area | The right-aligned 18px heading, e.g. *Orange CA*. Optional; the keyword section has none. |
| Project name / Project URL | The project link. |
| Project no. | The `projectnum` value in that project's own URL, e.g. `AGCS26-04773`. |
| Bid date, Location, Bin # | The second row of the project block. Bin # is often blank, which is not an error. |
| Source | The third row of the project block. |

A project can appear in more than one category. For example, *IFB Geotechnical Exploration Drilling* is listed under both *New Projects Received* and *Projects with New Addenda*. Both rows are kept and each is marked "also in: …". The stats show 49 rows and 48 unique projects.

**Using the results**

- The category chips and the search box filter the table.
- **Print, PDF, CSV and JSON include only the projects currently shown.**
- The page opens on the tab you used last. `/#email` and `/#pdf` link straight to a tab.

The email parser uses only Python's standard library (`email`, `html.parser`), so nothing extra is installed.

## API

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/` | Web UI |
| GET | `/api/health` | `{"status":"ok"}` |
| POST | `/api/export/pdf` | JSON body = the `/api/extract` response. Returns a branded landscape PDF report: a summary table plus every bid grouped by project, with a project never split across pages. |
| POST | `/api/extract` | multipart `file` (.pdf, max 20 MB). Returns JSON (see `samples/expected_output.json`). Errors: 400 bad file, 413 too large, 422 unparseable or scanned. |
| POST | `/api/extract-eml` | multipart `file` (.eml, max 20 MB). Returns JSON with `projects`, `categories` (name + count), `total_projects`, `unique_projects` and `report_date`. Errors: 400 bad file, 413 too large, 422 not a Daily Email Report. |
| POST | `/api/export/eml-pdf` | JSON body = `{source_file, report_date, projects}`. Returns a branded PDF grouped by category, then area. |

**Print icon** opens the browser's print dialog straight away, using a print-only layout: logo, summary table, footer, with the upload box and buttons hidden, in landscape. Browsers never let a web page print silently, so the dialog always appears; choose a printer and press Print. Expanded bidder rows are printed as shown.

**PDF icon** downloads `<file>-report.pdf`, built on the server with PyMuPDF (no extra dependency).

The tables in both PDF reports are drawn by `app/services/pdf_table.py` instead of PyMuPDF's HTML tables. The HTML tables ignore column widths, and `Story.write_with_links()` can hang on long reports. With the custom renderer:

- Column widths are exact.
- A row never splits across pages.
- Column headings and the current area repeat on every continuation page.
- Each project row carries exactly one link.

The CSV and JSON downloads are generated in the browser from the last extraction. CSV cells that start with `= + - @` are prefixed with an apostrophe, so spreadsheet apps won't execute them as formulas.

## Project layout

```
app/
  main.py               FastAPI routes, upload validation
  services/parser.py      Bid Results PDF parsing, validation, serialisation
  services/eml_parser.py  Daily Email Report (.eml) parsing
  services/pdf_report.py  branded PDF reports for both tabs
  services/pdf_table.py   fixed-column table renderer used by the reports
  static/                 index.html, style.css, app.js (PDF tab), eml.js (email tab),
                          tabs.js, logo.png (BIM Collaboration branding)
tests/
  fixtures/input.pdf          sample Bid Results PDF
  fixtures/daily_report.eml   sample Daily Email Report
  test_parser.py              PDF answer-key + reconciliation tests
  test_eml_parser.py          email parser tests
  test_api.py                 endpoint + report tests
samples/                expected JSON and CSV for the sample PDF
```

## Known limits

- Scanned or image-only PDFs are rejected with an "OCR required" message.
- Contractor names are kept exactly as printed, so `Dawson-Mauldin, LLC` and `Dawson Mauldin LLC` stay separate. Aggregating bids by company would need an alias table.
- Project links open login.onlineplanservice.com, so users without an account will see a login page.
- The email parser expects the onlineplanservice.com Daily Email Report layout. Forwarded emails usually keep that HTML and still parse. A plain-text-only email, or one whose layout has changed, is rejected with a clear message rather than parsed badly.
