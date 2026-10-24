// Pages are rebuilt every few hours, but a showing that has started shouldn't
// wait for the next build to go. Every minute this hides started showtimes,
// then anything they leave empty: a film's row, a day, a theatre's group, a
// date chip, and cards whose last showing has started.
(() => {
  // The build's fixed offset for Vancouver (render.fixed_zone), not the browser's rules.
  const ZONE = document.body?.dataset.zone || "America/Vancouver";
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });

  function nowLocal() {
    const p = Object.fromEntries(parts.formatToParts(new Date()).map((x) => [x.type, x.value]));
    return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
  }

  const live = (el) => el.querySelector(".time:not(.is-past)");

  function update() {
    const now = nowLocal();
    for (const time of document.querySelectorAll(".time[data-start]")) {
      time.classList.toggle("is-past", time.dataset.start <= now);
    }
    // Showtime rows, film-page days, then the day and theatre groups around them.
    for (const el of document.querySelectorAll(".row, .showing, .venue-group, [data-group]")) {
      if (el.querySelector(".time")) el.classList.toggle("is-past", !live(el));
    }
    for (const card of document.querySelectorAll(".card[data-last]")) {
      if (!card.closest("[data-live]")) card.classList.toggle("is-past", card.dataset.last <= now);
    }
    for (const link of document.querySelectorAll(".date-strip a")) {
      const target = document.querySelector(link.getAttribute("href"));
      link.classList.toggle("is-past", Boolean(target?.classList.contains("is-past")));
    }
    const today = now.slice(0, 10);
    for (const day of document.querySelectorAll(".cal-day[data-day]")) {
      day.classList.toggle("is-past", day.dataset.day < today);
    }
    window.cineclockFilters?.();
  }

  update();
  setInterval(update, 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) update();
  });
})();
