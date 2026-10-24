// Filters for the Films and Showtimes pages. They run in the browser, so the
// site stays plain files. Without JavaScript the filter bar stays hidden and
// everything is listed.
(() => {
  const form = document.querySelector(".filters");
  if (!form) return;
  const search = form.querySelector("input[type=search]");
  const chips = [...form.querySelectorAll(".chip")];
  const status = form.querySelector(".filter-status");
  const items = [...document.querySelectorAll("[data-item]")];
  form.hidden = false;

  // Shown in place of the list when nothing matches.
  const none = document.createElement("div");
  none.className = "no-results";
  none.hidden = true;
  none.setAttribute("role", "status");
  none.innerHTML = '<b></b><span>Try another spelling, or fewer filters.</span><button type="button" class="me-button">Clear filters</button>';
  form.after(none);

  const params = new URLSearchParams(location.search);
  for (const chip of chips) {
    if (params.getAll(chip.dataset.filter).includes(chip.dataset.value)) {
      chip.setAttribute("aria-pressed", "true");
    }
  }
  search.value = params.get("q") || "";

  // The Films page can be sorted; each item carries its sort values.
  const sort = form.querySelector("select[name=sort]");
  const list = document.querySelector("[data-sortable]");
  if (sort && params.get("sort")) sort.value = params.get("sort");

  function reorder() {
    if (!sort || !list) return;
    const key = "sort" + sort.value[0].toUpperCase() + sort.value.slice(1);
    // Dates sort soonest first; every other sort puts the biggest number first.
    const direction = sort.value === "next" || sort.value === "leaving" ? 1 : -1;
    const sorted = [...list.children].sort(
      (a, b) =>
        direction * (Number(a.dataset[key]) - Number(b.dataset[key])) ||
        Number(a.dataset.sortNext) - Number(b.dataset.sortNext)
    );
    list.append(...sorted);
  }

  const picked = (kind) =>
    chips
      .filter((c) => c.dataset.filter === kind && c.getAttribute("aria-pressed") === "true")
      .map((c) => c.dataset.value);

  const words = (el, key) => (el.dataset[key] || "").split(" ").filter(Boolean);

  function apply() {
    const venues = picked("venue");
    const tags = picked("tag");
    const genres = picked("genre");
    const q = search.value.trim().toLowerCase();
    let shown = 0;

    for (const item of items) {
      const itemTags = words(item, "tags");
      const match =
        !item.classList.contains("is-past") &&
        (!venues.length || venues.some((v) => words(item, "venues").includes(v))) &&
        tags.every((t) => itemTags.includes(t)) &&
        (!genres.length || genres.some((g) => words(item, "genres").includes(g))) &&
        (!q || item.dataset.title.includes(q));
      item.hidden = !match;
      shown += match;
    }

    // Hide headings for days and theatres left with nothing in them.
    for (const group of document.querySelectorAll("[data-group]")) {
      group.hidden = !group.querySelector("[data-item]:not([hidden]):not(.is-past)");
    }
    for (const link of document.querySelectorAll(".date-strip a")) {
      const target = document.querySelector(link.getAttribute("href"));
      link.hidden = Boolean(target && target.hidden);
    }

    // Show how many options are picked in each dropdown's label.
    for (const menu of form.querySelectorAll("details")) {
      const count = menu.querySelectorAll('.chip[aria-pressed="true"]').length;
      menu.querySelector(".count").textContent = count ? count : "";
      menu.classList.toggle("active", count > 0);
    }

    const filtering = venues.length || tags.length || genres.length || q;
    status.textContent = filtering && shown ? `${shown} showing` : "";
    form.querySelector(".clear").hidden = !filtering;
    none.hidden = !(filtering && !shown);
    none.querySelector("b").textContent = q ? `Nothing playing matches “${search.value.trim()}”` : "Nothing playing matches those filters";
    for (const strip of document.querySelectorAll(".date-tools")) strip.hidden = Boolean(filtering && !shown);

    const next = new URLSearchParams();
    if (sort && sort.value !== "next") next.set("sort", sort.value);
    venues.forEach((v) => next.append("venue", v));
    tags.forEach((t) => next.append("tag", t));
    genres.forEach((g) => next.append("genre", g));
    if (q) next.set("q", q);
    const query = next.toString();
    history.replaceState(null, "", (query ? `?${query}` : location.pathname) + location.hash);
  }

  window.cineclockFilters = apply;

  for (const chip of chips) {
    chip.addEventListener("click", () => {
      const on = chip.getAttribute("aria-pressed") === "true";
      chip.setAttribute("aria-pressed", on ? "false" : "true");
      apply();
    });
  }
  function clear() {
    chips.forEach((c) => c.setAttribute("aria-pressed", "false"));
    search.value = "";
    if (sort) sort.value = "next";
    reorder();
    apply();
  }
  form.querySelector(".clear").addEventListener("click", clear);
  none.querySelector("button").addEventListener("click", clear);
  // Close an open dropdown when another opens or you tap elsewhere.
  const menus = [...form.querySelectorAll("details")];
  for (const menu of menus) {
    menu.addEventListener("toggle", () => {
      if (menu.open) menus.filter((m) => m !== menu).forEach((m) => (m.open = false));
    });
  }
  document.addEventListener("click", (e) => {
    if (!form.contains(e.target)) menus.forEach((m) => (m.open = false));
  });
  search.addEventListener("input", apply);
  if (sort) {
    sort.addEventListener("change", () => {
      reorder();
      apply();
    });
  }
  form.addEventListener("submit", (e) => e.preventDefault());
  // Letterboxd marks (me.js) arrive after the page loads; filter again when they do.
  document.addEventListener("cineclock:personal", apply);
  reorder();
  apply();
})();
