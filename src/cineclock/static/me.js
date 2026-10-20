// Personal touches: a visitor's Letterboxd, the films they saved here, their
// IMDb watchlist, and the rating source they prefer.
//
// A connected Letterboxd user is remembered in localStorage as { user: "yogzoth" }.
// Profiles the site syncs daily load from me/<user>.json; anyone else is read
// through the relay (relay/worker.js), since Letterboxd won't let a browser read
// profiles itself. Saves and the IMDb list come from account.js. Every film on a
// page carries data-film="<slug>"; we mark the ones that match, then tell the
// filters (which listen for "cineclock:personal").
(() => {
  const root = document.body.dataset.root || "";
  const KEY = "cineclock.letterboxd";
  const IMDB = "cineclock.imdb";
  const RELAY = document.body.dataset.relay || "";

  const ICONS = {
    bookmark: '<path d="M6.5 3.5h11a1 1 0 0 1 1 1v16l-6.5-4.5-6.5 4.5v-16a1 1 0 0 1 1-1z"/>',
    eye: '<path d="M2.5 12s3.5-6.5 9.5-6.5 9.5 6.5 9.5 6.5-3.5 6.5-9.5 6.5S2.5 12 2.5 12z"/><circle cx="12" cy="12" r="2.5"/>',
    ticket: '<path d="M4 7a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v3a2 2 0 0 0 0 4v3a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-3a2 2 0 0 0 0-4z"/>',
  };
  const icon = (name, size = 12) =>
    `<svg class="icon" viewBox="0 0 24 24" width="${size}" height="${size}" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${ICONS[name]}</svg>`;

  function saved() {
    try {
      const value = JSON.parse(localStorage.getItem(KEY) || "null");
      return value && value.user ? value : null;
    } catch (e) {
      return null;
    }
  }

  function save(value) {
    try {
      if (value) localStorage.setItem(KEY, JSON.stringify(value));
      else localStorage.removeItem(KEY);
      return true;
    } catch (e) {
      return false;
    }
  }

  // A connected IMDb watchlist: { user: "ur12345678", ids, ready, error, at },
  // or { user: "file", ids } for an uploaded export.
  function imdbSaved() {
    try {
      const value = JSON.parse(localStorage.getItem(IMDB) || "null");
      return value && value.user ? value : null;
    } catch (e) {
      return null;
    }
  }

  function imdbSave(value) {
    try {
      if (value) localStorage.setItem(IMDB, JSON.stringify(value));
      else localStorage.removeItem(IMDB);
      return true;
    } catch (e) {
      return false;
    }
  }

  // The Worker keeps IMDb lists (relay/imdb.js); a GitHub job reads them from IMDb.
  async function imdbCall(method, path, body) {
    const hosts = [document.body.dataset.api, RELAY].filter(Boolean);
    for (const [i, host] of hosts.entries()) {
      try {
        const response = await fetch(host + path, {
          method,
          headers: body ? { "Content-Type": "application/json" } : {},
          body: body ? JSON.stringify(body) : undefined,
        });
        if (response.status === 404) throw new Error("missing");
        if (!response.ok) throw new Error("relay");
        const list = await response.json();
        return { ...list, at: Date.now() };
      } catch (error) {
        if (error.message === "missing" || i === hosts.length - 1) throw error;
      }
    }
  }

  const imdbConnect = (user) => imdbCall("POST", "/imdb/connect", { user });
  const imdbSync = (user) => imdbCall("POST", "/imdb/sync", { user });

  // Check every 15 seconds while a read is on its way, otherwise every few hours.
  // A list last read over half a day ago gets a fresh read asked for, once.
  let askedFresh = false;
  async function imdbFresh() {
    const value = imdbSaved();
    if (!value || value.user === "file") return value;
    const wait = value.ready && !value.syncing ? 3 * 3600 * 1000 : 15 * 1000;
    if (Date.now() - (value.at || 0) < wait) return value;
    try {
      const stale = value.updated && Date.now() - Date.parse(value.updated) > 12 * 3600 * 1000;
      if (stale && !askedFresh) {
        askedFresh = true;
        await imdbSync(value.user);
      }
      const fresh = await imdbCall("GET", `/imdb/${value.user}`);
      // A sync is done once the list says it was read after it was asked for.
      if (value.syncing && fresh.updated === value.updated && Date.now() - value.syncing < 3 * 60 * 1000) {
        fresh.syncing = value.syncing;
      }
      imdbSave(fresh);
      return fresh;
    } catch (e) {
      if (e.message === "missing") return imdbConnect(value.user).then((v) => (imdbSave(v), v), () => value);
      return value;
    }
  }

  const fetched = {};
  function getJSON(path) {
    fetched[path] ||= fetch(root + path, { cache: "no-cache" }).then((response) => {
      if (!response.ok) throw new Error(`${path}: ${response.status}`);
      return response.json();
    });
    fetched[path].catch(() => delete fetched[path]);
    return fetched[path];
  }

  // A film's place in a member's lists, in the same short keys the build uses:
  // w watchlist, s seen, r rating out of 10, d last diary date, old seen before
  // the diary feed reaches back.
  function statuses(profile, index) {
    const watchlist = new Set(profile.watchlist);
    const films = {};
    for (const film of index) {
      const lb = film.lb;
      if (!lb) continue;
      const found = {};
      if (watchlist.has(lb)) found.w = 1;
      if (lb in profile.watched) {
        found.s = 1;
        if (profile.watched[lb]) found.r = profile.watched[lb];
        if (profile.diary[lb]) found.d = profile.diary[lb];
        else if (profile.diary_since) found.old = 1;
      }
      if (Object.keys(found).length) films[film.slug] = found;
    }
    return films;
  }

  async function load(value = saved(), { fresh = false } = {}) {
    if (!value) return null;
    if (!fresh) {
      try {
        const data = await getJSON(`me/${encodeURIComponent(value.user)}.json`);
        return { user: value.user, films: data.films, updated: data.updated };
      } catch (e) {
        if (!RELAY) throw e;
      }
    }
    const response = await fetch(`${RELAY}/profile/${encodeURIComponent(value.user)}${fresh ? "?fresh=1" : ""}`);
    if (response.status === 404) throw new Error("missing");
    if (!response.ok) throw new Error("relay");
    const profile = await response.json();
    return { user: profile.user, films: statuses(profile, await getJSON("films.json")) };
  }

  // The finished cards (from films.json) for the films that pass a test.
  async function cards(films, test) {
    const index = await getJSON("films.json");
    return index
      .filter((f) => films[f.slug] && test(films[f.slug]))
      .map((f) => f.card.replaceAll("@root@", root))
      .join("");
  }

  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

  function stars(rating) {
    if (!rating) return "";
    return " " + "★".repeat(Math.floor(rating / 2)) + (rating % 2 ? "½" : "");
  }

  // The pills for one film: watchlist, and when (and how much) they liked it.
  function pills(status) {
    const out = [];
    if (status.p) out.push(["ticket", "Planned"]);
    if (status.w) out.push(["bookmark", "On watchlist"]);
    if (status.s) {
      const year = 365 * 24 * 3600 * 1000;
      let when = "Seen";
      if (status.d && Date.now() - Date.parse(status.d) < year) {
        const [, m, d] = status.d.split("-").map(Number);
        when = `Seen ${MONTHS[m - 1]} ${d}`;
      } else if (status.old || status.d) {
        when = "Seen a while ago";
      }
      out.push(["eye", when + stars(status.r)]);
    }
    return out;
  }

  function mark(films) {
    for (const el of document.querySelectorAll("[data-film]")) {
      const status = films[el.dataset.film];
      const item = el.closest("[data-item]") || el;
      // Start from the page's own tags and marks, so an unsaved film loses its mark.
      if (item.dataset.tags !== undefined) item.dataset.baseTags ??= item.dataset.tags;
      el.querySelectorAll(".card-mark, .card-mark-inline").forEach((m) => m.remove());
      if (!status) {
        if (item.dataset.baseTags !== undefined) item.dataset.tags = item.dataset.baseTags;
        const slot = el.querySelector(".personal");
        if (slot) slot.hidden = true;
        continue;
      }
      if (item.dataset.tags !== undefined) {
        const tags = item.dataset.baseTags.split(" ").filter(Boolean);
        if (status.w) tags.push("watchlist");
        if (status.p) tags.push("planned");
        if (status.s) tags.push("seen");
        item.dataset.tags = tags.join(" ");
      }
      const slot = el.querySelector(".personal");
      if (slot) {
        slot.innerHTML = pills(status)
          .map(([name, text]) => `<span class="tag tag-personal">${icon(name)}${text}</span>`)
          .join("");
        slot.hidden = false;
      }
      // On a card, a small mark before the title: a purple ticket for your
      // plans here, a green bookmark for a Letterboxd or IMDb watchlist.
      const [markIcon, markTitle, markKind] = status.p
        ? ["ticket", "In your plans", "plan"]
        : status.w
          ? ["bookmark", "On your watchlist", "watch"]
          : ["eye", "Seen", "seen"];
      const title = el.querySelector(".card-box .card-title");
      if (title) {
        title.insertAdjacentHTML(
          "afterbegin",
          `<span class="card-mark-inline is-${markKind}" title="${markTitle}">${icon(markIcon, 12)}</span>`
        );
        continue;
      }
      const art = el.querySelector(".card-art");
      if (art && !art.querySelector(".card-mark")) {
        art.insertAdjacentHTML(
          "beforeend",
          `<span class="card-mark" title="${status.w ? "On your watchlist" : "Seen"}">${icon(status.w ? "bookmark" : "eye", 14)}</span>`
        );
      }
    }
  }

  // Home: a shelf of watchlist films playing, drawn from films.json.
  async function shelf(films) {
    const spot = document.getElementById("mine-rail");
    if (!spot) return;
    // The build draws each card, so the shelf matches every other card on the site.
    const list = await cards(films, (f) => f.w || f.p);
    if (!list) {
      spot.innerHTML = "";
      return;
    }
    spot.innerHTML = `<section class="rail rail-personal"><div class="rail-head"><h2><a href="${root}films/?tag=watchlist">From your watchlist</a></h2>
      <a class="see-all" href="${root}films/?tag=watchlist" aria-label="See all from your watchlist"><svg class="icon" viewBox="0 0 24 24" width="22" height="22" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M9 5l7 7-7 7"/></svg></a></div>
      <ul class="rail-cards">${list}</ul></section>`;
    window.cineclockWhen?.();
  }

  // The home page's third tile. Signed out, it sells the account. Signed in, it
  // never repeats the watchlist rail under it: your next plan, else the
  // watchlist film about to leave, else how to get started.
  const plainText = (html) => new DOMParser().parseFromString(html, "text/html").body.textContent;
  function dayName(iso) {
    const today = new Date().toLocaleDateString("en-CA", { timeZone: "America/Vancouver" });
    const tomorrow = new Date(Date.now() + 864e5).toLocaleDateString("en-CA", { timeZone: "America/Vancouver" });
    if (iso === today) return "today";
    if (iso === tomorrow) return "tomorrow";
    return new Date(`${iso}T12:00`).toLocaleDateString("en-CA", { weekday: "long", month: "short", day: "numeric" });
  }

  async function hubTile(films) {
    const tile = document.querySelector("[data-hub-mine]");
    if (!tile) return;
    const account = window.cineclockAccount?.current;
    const connected = Boolean(saved() || imdbSaved() || account?.imdb?.length);
    let title = "Your watchlist, here";
    let sub = "Sign in to connect Letterboxd or IMDb";
    let href = `${root}me/`;
    if (account) {
      const index = await getJSON("films.json").catch(() => []);
      const plans = index.filter((f) => films[f.slug]?.p).sort((a, b) => a.days[0].localeCompare(b.days[0]));
      const week = new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10);
      const leaving = index
        .filter((f) => films[f.slug]?.w && f.last <= week)
        .sort((a, b) => a.last.localeCompare(b.last));
      if (plans.length) {
        title = `Next plan: ${plainText(plans[0].title)}`;
        sub = `Playing ${dayName(plans[0].days[0])}${plans.length > 1 ? `, and ${plans.length - 1} more planned` : ""}`;
        href = `${root}films/${plans[0].slug}/`;
      } else if (leaving.length) {
        title = `Last chance: ${plainText(leaving[0].title)}`;
        sub = `From your watchlist, last showing ${dayName(leaving[0].last)}`;
        href = `${root}films/${leaving[0].slug}/`;
      } else if (connected) {
        title = "Plan a night out";
        sub = "Tap Plan to go on a film to keep it here";
      } else {
        title = "Connect your watchlist";
        sub = "Letterboxd or IMDb, from your dashboard";
      }
    }
    tile.href = href;
    const planned = title.startsWith("Next plan");
    tile.querySelector("[data-hub-mine-icon]").hidden = !planned;
    tile.querySelector(".hub-logos").hidden = planned;
    tile.querySelector("[data-hub-mine-title]").textContent = title;
    tile.querySelector("[data-hub-mine-sub]").textContent = sub;
  }

  function show(connected) {
    document.querySelectorAll("[data-needs-me]").forEach((el) => (el.hidden = !connected));
    document.querySelectorAll("[data-needs-no-me]").forEach((el) => (el.hidden = connected));
  }

  // Your cineclock plans (p) and an IMDb watchlist (w) join the Letterboxd marks.
  async function withSaves(films) {
    const account = window.cineclockAccount;
    if (!account) return films;
    await imdbFresh();
    const savedFilms = new Set(account.savedList("film"));
    const imdb = new Set([...(account.current?.imdb || []), ...(imdbSaved()?.ids || [])]);
    if (!savedFilms.size && !imdb.size) return films;
    const out = { ...films };
    for (const film of await getJSON("films.json")) {
      if (savedFilms.has(film.slug) || (film.imdb && imdb.has(film.imdb))) {
        const status = { ...out[film.slug] };
        if (savedFilms.has(film.slug)) status.p = 1;
        if (film.imdb && imdb.has(film.imdb)) status.w = 1;
        out[film.slug] = status;
      }
    }
    return out;
  }

  // Scores from the visitor's chosen source, on every card and in Highly rated.
  async function useSource() {
    const source = window.cineclockAccount?.source() || "letterboxd";
    if (source === "letterboxd" && !document.body.dataset.ratedBy) return;
    document.body.dataset.ratedBy = source;
    const index = await getJSON("films.json");
    const bySlug = Object.fromEntries(index.map((f) => [f.slug, f]));
    const scoreOf = (film) => film?.scores?.[source];

    const top = document.querySelector('[aria-labelledby="rail-highly-rated"] .rail-cards');
    if (top) {
      const ranked = index
        // Same bar as the build: well-known films only, so a 100% from five reviews doesn't top it.
        .filter((f) => scoreOf(f) != null && (f.lb_count || 0) >= 1000)
        .sort((a, b) => scoreOf(b) - scoreOf(a) || (b.lb_count || 0) - (a.lb_count || 0))
        .slice(0, 15);
      if (ranked.length) top.innerHTML = ranked.map((f) => f.card.replaceAll("@root@", root)).join("");
    }

    for (const el of document.querySelectorAll("[data-film]")) {
      const film = bySlug[el.dataset.film];
      const shown = el.querySelector(".card-rating");
      const wanted = film?.rated?.[source];
      if (shown && wanted) shown.outerHTML = wanted.replaceAll("@root@", root);
      const item = el.closest("[data-sort-rated]");
      if (item) item.dataset.sortRated = scoreOf(film) ?? 0;
    }
    window.cineclockWhen?.();
  }

  let lastFilms = null;
  async function start() {
    // Signed in on a new device: bring back the Letterboxd saved to the account.
    const fromAccount = window.cineclockAccount?.current?.letterboxd;
    if (fromAccount && !saved()) save({ user: fromAccount });
    const value = saved();
    show(Boolean(value));
    // Connected: the Letterboxd button shows whose profile it is.
    if (value) document.querySelectorAll("[data-me-name]").forEach((el) => (el.textContent = value.user));
    let films = {};
    let me = null;
    if (value) {
      try {
        me = await load(value);
        films = me?.films || {};
      } catch (e) {
        // A profile that stopped syncing shouldn't break the page.
        show(false);
      }
    }
    try {
      films = await withSaves(films);
      await useSource();
    } catch (e) {}
    hubTile(films).catch(() => {});
    if (!Object.keys(films).length && !lastFilms) return;
    lastFilms = films;
    mark(films);
    await shelf(films);
    mark(films);
    // The shelf's cards arrive after the first score swap, so swap again.
    await useSource().catch(() => {});
    me = { ...(me || {}), films };
    window.cineclockPersonal = me;
    document.dispatchEvent(new CustomEvent("cineclock:personal", { detail: me }));
  }

  window.cineclockMe = { saved, save, load, cards, mark, root, withSaves, getJSON, imdbSaved, imdbSave, imdbConnect, imdbSync, imdbFresh };
  document.addEventListener("cineclock:account", () => start());
  start();
})();
