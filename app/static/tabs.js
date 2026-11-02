(() => {
  "use strict";

  const tabs = Array.from(document.querySelectorAll(".tab"));
  const heading = document.getElementById("page-heading");
  const STORE_KEY = "bid-extractor-tab";

  function activate(tab, { focus = false, remember = true } = {}) {
    tabs.forEach((t) => {
      const selected = t === tab;
      t.setAttribute("aria-selected", String(selected));
      t.tabIndex = selected ? 0 : -1;
      document.getElementById(t.getAttribute("aria-controls")).hidden = !selected;
    });
    heading.textContent = tab.dataset.heading;
    document.title = `${tab.dataset.heading} | BIM Collaboration LLC`;
    if (focus) tab.focus();
    if (remember) {
      try { localStorage.setItem(STORE_KEY, tab.id); } catch { /* storage unavailable */ }
      if (location.hash !== tab.dataset.hash) history.replaceState(null, "", tab.dataset.hash);
    }
  }

  tabs.forEach((tab, i) => {
    tab.addEventListener("click", () => activate(tab));
    tab.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
      if (!step) return;
      e.preventDefault();
      activate(tabs[(i + step + tabs.length) % tabs.length], { focus: true });
    });
  });

  // Initial tab: URL hash (#email / #pdf) wins, then the last tab used, then PDF.
  let initial = tabs.find((t) => t.dataset.hash === location.hash);
  if (!initial) {
    let saved = null;
    try { saved = localStorage.getItem(STORE_KEY); } catch { /* storage unavailable */ }
    initial = tabs.find((t) => t.id === saved);
  }
  activate(initial || tabs[0], { remember: false });
})();
