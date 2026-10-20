// The home page's On soon row, kept current between builds. Each card lists its
// film's showtimes (data-times, Vancouver time). A film whose showing has
// started drops out, and if it plays again it goes back in line at that time,
// with its date line redone. Spares wait at the end to keep the row full.
(() => {
  // The build's fixed offset for Vancouver (render.fixed_zone), not the browser's rules.
  const ZONE = document.body?.dataset.zone || "America/Vancouver";
  const row = document.querySelector("[data-live]");
  if (!row) return;
  const length = Number(row.dataset.length) || 15;
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

  function update() {
    const now = nowLocal();
    const cards = [...row.children].map((card) => ({
      card,
      next: (card.dataset.times || "").split(" ").find((t) => t > now),
    }));
    for (const { card, next } of cards) {
      if (!next) {
        card.remove();
        continue;
      }
      const time = card.querySelector(".card-next time");
      if (time && time.getAttribute("datetime") !== next) {
        time.setAttribute("datetime", next);
        time.textContent = window.cineclockWhen?.text(next, "card", now.slice(0, 10)) || time.textContent;
      }
    }
    const live = cards.filter((c) => c.next).sort((a, b) => a.next.localeCompare(b.next));
    live.forEach(({ card }, i) => {
      card.hidden = i >= length;
      row.append(card);
    });
  }

  update();
  setInterval(update, 60 * 1000);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) update();
  });
})();
