(() => {
  "use strict";

  const MAX_BYTES = 20 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);
  const els = {
    drop: $("dropzone"), input: $("file-input"), info: $("file-info"),
    extract: $("extract-btn"), clear: $("clear-btn"), spinner: $("spinner"), error: $("error"),
    results: $("results"), rows: $("rows"), empty: $("empty"),
    statProjects: $("stat-projects"), statBidders: $("stat-bidders"), statFlagged: $("stat-flagged"),
    dlJson: $("dl-json"), dlCsv: $("dl-csv"), dlBids: $("dl-bids"),
    printBtn: $("print-btn"), pdfBtn: $("pdf-btn"), printMeta: $("print-meta"),
  };

  let selectedFile = null;
  let lastResult = null;

  const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD" });
  const money = (v) => (v === null || v === undefined ? "—" : usd.format(v));

  const FLAG_LABELS = {
    first_listed_not_lowest: "first listed ≠ lowest",
    no_prices_listed: "no prices listed",
    no_bidders: "no bidders",
    missing_location: "no location",
    missing_date: "no date",
    missing_url: "no link",
    bidder_missing_amount: "bidder missing amount",
  };

  // ---------- file selection ----------
  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function selectFile(file) {
    hideError();
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".pdf")) return showError("Only PDF files are accepted.");
    if (file.size > MAX_BYTES) return showError("File exceeds the 20 MB limit.");
    selectedFile = file;
    els.info.innerHTML = "";
    const b = document.createElement("b");
    b.textContent = file.name;
    els.info.append("Selected: ", b, ` (${formatSize(file.size)})`);
    els.info.hidden = false;
    els.extract.disabled = false;
  }

  els.input.addEventListener("change", (e) => selectFile(e.target.files[0]));
  els.drop.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); els.input.click(); }
  });
  ["dragenter", "dragover"].forEach((t) => els.drop.addEventListener(t, (e) => {
    e.preventDefault(); els.drop.classList.add("over");
  }));
  ["dragleave", "drop"].forEach((t) => els.drop.addEventListener(t, (e) => {
    e.preventDefault(); els.drop.classList.remove("over");
  }));
  els.drop.addEventListener("drop", (e) => selectFile(e.dataTransfer.files[0]));

  // ---------- extraction ----------
  els.extract.addEventListener("click", async () => {
    if (!selectedFile) return;
    hideError();
    setBusy(true);
    try {
      const form = new FormData();
      form.append("file", selectedFile);
      const res = await fetch("/api/extract", { method: "POST", body: form });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      lastResult = body;
      render(body);
    } catch (err) {
      showError(err.message || "Extraction failed.");
    } finally {
      setBusy(false);
    }
  });

  els.clear.addEventListener("click", () => {
    lastResult = null;
    selectedFile = null;
    els.input.value = "";
    els.info.hidden = true;
    els.results.hidden = true;
    els.rows.innerHTML = "";
    els.extract.disabled = true;
    els.clear.hidden = true;
    hideError();
  });

  function setBusy(busy) {
    els.spinner.hidden = !busy;
    els.extract.disabled = busy || !selectedFile;
  }
  function showError(msg) { els.error.textContent = msg; els.error.hidden = false; }
  function hideError() { els.error.hidden = true; els.error.textContent = ""; }

  // ---------- rendering (textContent only; never innerHTML with PDF data) ----------
  function cell(tag, text, cls) {
    const td = document.createElement(tag);
    if (cls) td.className = cls;
    if (text !== undefined && text !== null) td.textContent = text;
    return td;
  }

  function safeUrl(u) {
    try {
      const url = new URL(u);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch { return null; }
  }

  function render(data) {
    els.statProjects.textContent = data.total_projects;
    els.statBidders.textContent = data.total_bidders;
    els.statFlagged.textContent = data.flagged_projects;
    els.rows.innerHTML = "";
    els.results.hidden = false;
    els.clear.hidden = false;
    els.empty.hidden = data.projects.length > 0;

    data.projects.forEach((p) => {
      const tr = document.createElement("tr");
      tr.className = "project";

      const nameTd = document.createElement("td");
      const href = p.project_url && safeUrl(p.project_url);
      const name = document.createElement(href ? "a" : "span");
      name.className = "pname";
      name.textContent = p.project_name;
      if (href) {
        name.href = href;
        name.target = "_blank";
        name.rel = "noopener noreferrer";
        name.title = "Opens onlineplanservice.com (login may be required)";
        name.addEventListener("click", (e) => e.stopPropagation());
      }
      nameTd.append(name);
      p.flags.forEach((f) => {
        const chip = document.createElement("div");
        chip.className = "flag";
        chip.textContent = FLAG_LABELS[f] || f;
        nameTd.append(document.createElement("br"), chip);
      });
      tr.append(nameTd);

      tr.append(cell("td", p.location ?? "—", p.location ? "" : "muted"));
      tr.append(cell("td", p.first_contractor ?? "—"));
      tr.append(cell("td", money(p.bid_amount), "num"));

      const differs = p.lowest_contractor && p.lowest_contractor !== p.first_contractor;
      const lowTd = cell("td", p.lowest_contractor ? `${p.lowest_contractor} (${money(p.lowest_amount)})` : "—",
        differs ? "diff" : "muted");
      if (!differs && p.lowest_contractor) lowTd.textContent = "same as first";
      tr.append(lowTd);

      tr.append(cell("td", p.project_date ?? "—"));
      tr.append(cell("td", String(p.bidder_count), "num"));

      const detail = buildDetail(p);
      detail.hidden = true;
      tr.addEventListener("click", () => { detail.hidden = !detail.hidden; });
      els.rows.append(tr, detail);
    });
  }

  function buildDetail(p) {
    const tr = document.createElement("tr");
    tr.className = "detail";
    const td = document.createElement("td");
    td.colSpan = 7;
    if (!p.bidders.length) {
      td.textContent = "No bidders were found under this project.";
    } else {
      const lowest = p.lowest_amount;
      const t = document.createElement("table");
      const head = document.createElement("tr");
      head.append(cell("th", "#"), cell("th", "Contractor"), cell("th", "Bid amount", "num"));
      t.append(head);
      p.bidders.forEach((b, i) => {
        const row = document.createElement("tr");
        if (b.bid_amount !== null && b.bid_amount === lowest) row.className = "low";
        const amt = b.amount_status === "no_price" ? "no price" : b.amount_status === "missing" ? "missing" : money(b.bid_amount);
        row.append(cell("td", String(i + 1)), cell("td", b.contractor_name), cell("td", amt, "num"));
        t.append(row);
      });
      td.append(t);
    }
    tr.append(td);
    return tr;
  }

  // ---------- exports (generated in the browser) ----------
  function download(name, content, type) {
    const blob = new Blob([content], { type });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  }

  // Prevent CSV/formula injection: prefix cells that spreadsheets would evaluate.
  function csvCell(v) {
    if (v === null || v === undefined) return "";
    let s = String(v);
    if (typeof v === "string" && /^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }
  const amountStr = (v) => (v === null || v === undefined ? "" : v.toFixed(2));
  const toCsv = (header, rows) => [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
  const baseName = () => (lastResult?.source_file || "bid-results").replace(/\.pdf$/i, "");

  // ---------- print + PDF report ----------
  els.printBtn.addEventListener("click", () => {
    if (!lastResult) return;
    // A web page cannot skip the print dialog; this opens it straight away with a print-only layout.
    els.printMeta.textContent =
      `Source: ${lastResult.source_file} · ${lastResult.total_projects} projects · ` +
      `${lastResult.total_bidders} bid rows · ${lastResult.flagged_projects} flagged · ` +
      `Printed ${new Date().toLocaleString()}`;
    window.print();
  });

  els.pdfBtn.addEventListener("click", async () => {
    if (!lastResult) return;
    hideError();
    els.pdfBtn.disabled = true;
    els.pdfBtn.classList.add("busy");
    try {
      const res = await fetch("/api/export/pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(lastResult),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `PDF export failed (${res.status})`);
      }
      download(`${baseName()}-report.pdf`, await res.blob(), "application/pdf");
    } catch (err) {
      showError(err.message || "PDF export failed.");
    } finally {
      els.pdfBtn.disabled = false;
      els.pdfBtn.classList.remove("busy");
    }
  });

  els.dlJson.addEventListener("click", () => {
    if (!lastResult) return;
    download(`${baseName()}.json`, JSON.stringify(lastResult, null, 2), "application/json");
  });

  els.dlCsv.addEventListener("click", () => {
    if (!lastResult) return;
    const header = ["project_date", "project_name", "project_url", "location", "first_contractor", "bid_amount",
      "lowest_contractor", "lowest_amount", "bidder_count", "flags"];
    const rows = lastResult.projects.map((p) => [p.project_date, p.project_name, p.project_url, p.location,
      p.first_contractor, amountStr(p.bid_amount), p.lowest_contractor, amountStr(p.lowest_amount),
      p.bidder_count, p.flags.join(";")]);
    download(`${baseName()}-projects.csv`, toCsv(header, rows), "text/csv;charset=utf-8");
  });

  els.dlBids.addEventListener("click", () => {
    if (!lastResult) return;
    const header = ["project_date", "project_name", "location", "rank_listed", "contractor_name", "bid_amount", "amount_status"];
    const rows = lastResult.projects.flatMap((p) => p.bidders.map((b, i) => [p.project_date, p.project_name,
      p.location, i + 1, b.contractor_name, amountStr(b.bid_amount), b.amount_status]));
    download(`${baseName()}-all-bids.csv`, toCsv(header, rows), "text/csv;charset=utf-8");
  });
})();
