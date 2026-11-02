(() => {
  "use strict";

  const MAX_BYTES = 20 * 1024 * 1024;
  const $ = (id) => document.getElementById(id);
  const els = {
    drop: $("eml-dropzone"), input: $("eml-file-input"), info: $("eml-file-info"),
    extract: $("eml-extract-btn"), clear: $("eml-clear-btn"), spinner: $("eml-spinner"), error: $("eml-error"),
    results: $("eml-results"), rows: $("eml-rows"), empty: $("eml-empty"), showing: $("eml-showing"),
    chips: $("eml-category-chips"), search: $("eml-search"),
    statProjects: $("eml-stat-projects"), statUnique: $("eml-stat-unique"),
    statCategories: $("eml-stat-categories"), statFlagged: $("eml-stat-flagged"),
    printBtn: $("eml-print-btn"), pdfBtn: $("eml-pdf-btn"), dlJson: $("eml-dl-json"), dlCsv: $("eml-dl-csv"),
    printMeta: $("print-meta"),
  };

  const FLAG_LABELS = {
    missing_project_no: "no project no.",
    missing_bid_date: "no bid date",
    missing_location: "no location",
    missing_source: "no source",
  };

  let selectedFile = null;
  let result = null;
  let activeCategory = null; // null = all
  let query = "";

  // ---------- file selection ----------
  function formatSize(bytes) {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
  }

  function selectFile(file) {
    hideError();
    if (!file) return;
    if (!file.name.toLowerCase().endsWith(".eml")) {
      return showError("Only .eml files are accepted. Save the email as a file first (see the note above).");
    }
    if (file.size > MAX_BYTES) return showError("File exceeds the 20 MB limit.");
    selectedFile = file;
    els.info.textContent = "";
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
      const res = await fetch("/api/extract-eml", { method: "POST", body: form });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail || `Request failed (${res.status})`);
      result = body;
      activeCategory = null;
      query = "";
      els.search.value = "";
      renderChips();
      render();
    } catch (err) {
      showError(err.message || "Extraction failed.");
    } finally {
      setBusy(false);
    }
  });

  els.clear.addEventListener("click", () => {
    result = null;
    selectedFile = null;
    els.input.value = "";
    els.info.hidden = true;
    els.results.hidden = true;
    els.rows.textContent = "";
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

  // ---------- filtering ----------
  function visibleProjects() {
    if (!result) return [];
    const q = query.trim().toLowerCase();
    return result.projects.filter((p) => {
      if (activeCategory && p.category !== activeCategory) return false;
      if (!q) return true;
      return [p.project_name, p.project_no, p.location, p.source, p.area, p.bin_no, p.bid_date]
        .some((v) => v && String(v).toLowerCase().includes(q));
    });
  }

  function renderChips() {
    els.chips.textContent = "";
    const all = [{ name: null, label: "All", count: result.total_projects }]
      .concat(result.categories.map((c) => ({ name: c.name, label: c.name, count: c.count })));
    all.forEach((c) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip";
      btn.setAttribute("aria-pressed", String(c.name === activeCategory));
      btn.textContent = `${c.label} `;
      const n = document.createElement("span");
      n.className = "chip-count";
      n.textContent = c.count;
      btn.append(n);
      btn.addEventListener("click", () => {
        activeCategory = c.name;
        renderChips();
        render();
      });
      els.chips.append(btn);
    });
  }

  let searchTimer = null;
  els.search.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { query = els.search.value; render(); }, 120);
  });

  // ---------- rendering (textContent only; never innerHTML with email data) ----------
  function cell(text, cls) {
    const td = document.createElement("td");
    if (cls) td.className = cls;
    td.textContent = text === null || text === undefined || text === "" ? "—" : text;
    if (text === null || text === undefined || text === "") td.classList.add("muted");
    return td;
  }

  function safeUrl(u) {
    try {
      const url = new URL(u);
      return url.protocol === "https:" || url.protocol === "http:" ? url.href : null;
    } catch { return null; }
  }

  function render() {
    const projects = visibleProjects();
    els.statProjects.textContent = result.total_projects;
    els.statUnique.textContent = result.unique_projects;
    els.statCategories.textContent = result.categories.length;
    els.statFlagged.textContent = result.flagged_projects;
    els.showing.textContent = `Showing ${projects.length} of ${result.total_projects} projects.`;
    els.results.hidden = false;
    els.clear.hidden = false;
    els.empty.hidden = projects.length > 0;
    els.rows.textContent = "";

    let category, area;
    projects.forEach((p) => {
      if (p.category !== category) {
        category = p.category;
        area = undefined;
        const count = projects.filter((x) => x.category === category).length;
        const tr = document.createElement("tr");
        tr.className = "cat-row";
        const td = document.createElement("td");
        td.colSpan = 6;
        td.textContent = `${category} `;
        const n = document.createElement("span");
        n.className = "chip-count";
        n.textContent = count;
        td.append(n);
        tr.append(td);
        els.rows.append(tr);
      }
      if (p.area !== area) {
        area = p.area;
        if (area) {
          const tr = document.createElement("tr");
          tr.className = "area-row";
          const td = document.createElement("td");
          td.colSpan = 6;
          td.textContent = area;
          tr.append(td);
          els.rows.append(tr);
        }
      }

      const tr = document.createElement("tr");
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
      }
      nameTd.append(name);
      if (p.also_in && p.also_in.length) {
        const chip = document.createElement("div");
        chip.className = "flag info";
        chip.textContent = `also in: ${p.also_in.join(", ")}`;
        nameTd.append(chip);
      }
      (p.flags || []).forEach((f) => {
        const chip = document.createElement("div");
        chip.className = "flag";
        chip.textContent = FLAG_LABELS[f] || f;
        nameTd.append(chip);
      });
      tr.append(
        nameTd,
        cell(p.project_no, "mono"),
        cell(p.bid_date, "nowrap"),
        cell(p.location),
        cell(p.bin_no),
        cell(p.source),
      );
      els.rows.append(tr);
    });
  }

  // ---------- exports ----------
  function download(name, content, type) {
    const blob = content instanceof Blob ? content : new Blob([content], { type });
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
    if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }
  const toCsv = (header, rows) => [header, ...rows].map((r) => r.map(csvCell).join(",")).join("\r\n") + "\r\n";
  const baseName = () => (result?.source_file || "daily-report").replace(/\.eml$/i, "");

  function filteredResult() {
    const projects = visibleProjects();
    return { ...result, projects, exported_projects: projects.length };
  }

  els.dlJson.addEventListener("click", () => {
    if (!result) return;
    download(`${baseName()}.json`, JSON.stringify(filteredResult(), null, 2), "application/json");
  });

  els.dlCsv.addEventListener("click", () => {
    if (!result) return;
    const header = ["category", "area", "project_name", "project_url", "project_no", "bid_date",
      "location", "bin_no", "source", "also_in", "flags"];
    const rows = visibleProjects().map((p) => [p.category, p.area, p.project_name, p.project_url, p.project_no,
      p.bid_date, p.location, p.bin_no, p.source, (p.also_in || []).join("; "), (p.flags || []).join(";")]);
    download(`${baseName()}.csv`, toCsv(header, rows), "text/csv;charset=utf-8");
  });

  els.printBtn.addEventListener("click", () => {
    if (!result) return;
    const shown = visibleProjects().length;
    els.printMeta.textContent =
      `Source: ${result.source_file}` + (result.report_date ? ` · Report date ${result.report_date}` : "") +
      ` · ${shown} of ${result.total_projects} projects` +
      (activeCategory ? ` · ${activeCategory}` : "") + (query.trim() ? ` · search "${query.trim()}"` : "") +
      ` · Printed ${new Date().toLocaleString()}`;
    window.print();
  });

  els.pdfBtn.addEventListener("click", async () => {
    if (!result) return;
    hideError();
    els.pdfBtn.disabled = true;
    els.pdfBtn.classList.add("busy");
    try {
      const res = await fetch("/api/export/eml-pdf", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source_file: result.source_file, report_date: result.report_date,
          projects: visibleProjects() }),
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
})();
