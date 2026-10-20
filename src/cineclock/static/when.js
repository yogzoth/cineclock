// Pages are rebuilt every few hours, so a page built last night still says
// "Tomorrow" for today's shows. Each date label carries its real date, and this
// redoes Today and Tomorrow from the visitor's own day in Vancouver.
(() => {
  // The build's fixed offset for Vancouver (render.fixed_zone), not the browser's rules.
  const ZONE = document.body?.dataset.zone || "America/Vancouver";
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const DAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const vancouver = new Intl.DateTimeFormat("en-CA", {
    timeZone: ZONE,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  });

  function todayIso(at = new Date()) {
    const part = Object.fromEntries(vancouver.formatToParts(at).map((p) => [p.type, p.value]));
    return `${part.year}-${part.month}-${part.day}`;
  }

  // Whole days since 1970, so two dates subtract to a day count.
  const dayNumber = (iso) => Date.UTC(+iso.slice(0, 4), +iso.slice(5, 7) - 1, +iso.slice(8, 10)) / 864e5;

  function clock(hhmm) {
    const [h, m] = hhmm.split(":").map(Number);
    return `${h % 12 || 12}:${String(m).padStart(2, "0")} ${h < 12 ? "am" : "pm"}`;
  }

  function text(value, fmt, today) {
    const iso = value.slice(0, 10);
    const day = new Date(dayNumber(iso) * 864e5);
    const gap = dayNumber(iso) - dayNumber(today);
    const near = gap === 0 ? "Today" : gap === 1 ? "Tomorrow" : null;
    const month = MONTHS[day.getUTCMonth()];
    const date = day.getUTCDate();
    const weekday = DAYS[day.getUTCDay()];
    // Within the coming week a card names the weekday: "Saturday 24, 8:15 pm".
    const thisWeek = gap >= 0 && gap < 7 ? `${weekday} ${date}` : null;
    if (fmt === "card") return `${near || thisWeek || `${month} ${date}`}, ${clock(value.slice(11, 16))}`;
    if (fmt === "ends") return `Ends ${near ? near.toLowerCase() : `${weekday.slice(0, 3)} ${date}`}`;
    if (fmt === "day") return `${near || weekday}, ${month} ${date}`;
    if (near) return near;
    // Past this month the strip needs the month too.
    if (iso.slice(0, 7) !== today.slice(0, 7)) return `${weekday.slice(0, 3)} ${month} ${date}`;
    return `${weekday.slice(0, 3)} ${date}`;
  }

  function relabel() {
    const today = todayIso();
    document.querySelectorAll("time[data-fmt]").forEach((el) => {
      el.textContent = text(el.getAttribute("datetime"), el.dataset.fmt, today);
    });
    document.querySelectorAll(".cal-day[data-day]").forEach((el) => {
      el.classList.toggle("today", el.dataset.day === today);
    });
    document.querySelectorAll("[data-tonight]").forEach((el) => {
      el.textContent = el.dataset.tonight === today ? "Tonight" : "Up next";
    });
  }

  window.cineclockWhen = relabel;
  relabel.text = text;
  relabel.todayIso = todayIso;
  relabel();
  // A tab left open overnight catches up when it comes back.
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) relabel();
  });
})();
