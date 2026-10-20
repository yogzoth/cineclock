// Home page hero: swipe or tap the arrows to move between highlights, and it
// wraps around. Without JavaScript the slides still scroll sideways on their own.
(() => {
  // The build's fixed offset for Vancouver (render.fixed_zone), not the browser's rules.
  const ZONE = document.body?.dataset.zone || "America/Vancouver";
  const hero = document.querySelector(".hero");
  if (!hero) return;
  const track = hero.querySelector(".hero-track");
  const SHOWN = 3;

  // First comes one of tonight's films, picked at random from those with a
  // showing still to come; once tonight's are over, one of tomorrow's. The other
  // two are drawn at random from the films coming up, so each visit turns up
  // something different.
  // Films shown on the last few visits sit out while there are others to show,
  // so reloading turns up new ones instead of the same favourites.
  const RECENT = "cineclock.hero.recent";
  let recent = [];
  try {
    recent = JSON.parse(localStorage.getItem(RECENT) || "[]");
  } catch (e) {}
  const fresh = (list, keep = 1) => {
    const unseen = list.filter((el) => !recent.includes(el.dataset.film));
    return unseen.length >= keep ? unseen : list;
  };

  const all = [...track.children];
  const day = (t) => new Date(t).toLocaleDateString("en-CA", { timeZone: ZONE });
  const live = all.filter((el) => el.dataset.until && Date.parse(el.dataset.until) > Date.now());
  const soonest = live.length ? day(Math.min(...live.map((el) => Date.parse(el.dataset.until)))) : null;
  const choices = fresh(live.filter((el) => day(Date.parse(el.dataset.until)) === soonest));
  const lead = choices[Math.floor(Math.random() * choices.length)];
  const pool = fresh(
    all.filter((el) => !el.dataset.until && el.dataset.film !== lead?.dataset.film),
    SHOWN,
  );
  for (let i = pool.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [pool[i], pool[j]] = [pool[j], pool[i]];
  }
  const kept = [lead, ...pool].filter(Boolean).slice(0, SHOWN);
  try {
    const shownNow = kept.map((el) => el.dataset.film);
    localStorage.setItem(RECENT, JSON.stringify([...shownNow, ...recent.filter((f) => !shownNow.includes(f))].slice(0, 12)));
  } catch (e) {}
  if (lead) {
    const until = Date.parse(lead.dataset.until);
    const label =
      day(until) === day(Date.now()) ? "Tonight"
      : day(until) === day(Date.now() + 864e5) ? "Tomorrow"
      : new Date(until).toLocaleDateString("en-CA", { weekday: "long", timeZone: ZONE });
    const eyebrow = lead.querySelector(".eyebrow");
    if (eyebrow) eyebrow.lastChild.textContent = label;
  }
  all.forEach((el) => el.remove());
  kept.forEach((el, n) => {
    el.setAttribute("aria-label", `${n + 1} of ${kept.length}: ${el.getAttribute("aria-label")}`);
    track.append(el);
  });
  kept[0]?.querySelector("img")?.removeAttribute("loading");
  const dotRow = hero.querySelector(".hero-dots");
  if (dotRow) {
    // Built as elements, not HTML text: titles come from theatre sites, and a
    // quote in one would otherwise break out of the attribute.
    dotRow.replaceChildren(
      ...kept.map((el) => {
        const dot = document.createElement("button");
        dot.type = "button";
        dot.setAttribute("aria-label", `Show ${el.querySelector(".spotlight-title")?.firstChild?.textContent.trim() || "film"}`);
        return dot;
      }),
    );
  }

  const slides = [...track.children];
  if (slides.length < 2) return;

  // A copy of the last slide goes before the first and a copy of the first after
  // the last, so moving past either end slides on smoothly, then quietly jumps
  // back to the real slide.
  const copy = (el) => {
    const c = el.cloneNode(true);
    c.setAttribute("aria-hidden", "true");
    c.tabIndex = -1;
    c.querySelector("img")?.removeAttribute("loading");
    return c;
  };
  track.prepend(copy(slides[slides.length - 1]));
  track.append(copy(slides[0]));
  hero.classList.add("is-carousel");

  const dots = [...hero.querySelectorAll(".hero-dots button")];
  hero.querySelectorAll("[hidden]").forEach((el) => (el.hidden = false));

  let pos = 1;
  // Slides are narrower than the hero, so move by each slide's real position.
  const place = (animate, offset = 0) => {
    track.style.transition = animate ? "" : "none";
    track.style.transform = `translateX(${offset - track.children[pos].offsetLeft}px)`;
  };
  const show = (n) => {
    pos = n;
    place(true);
    const real = (pos - 1 + slides.length) % slides.length;
    dots.forEach((d, i) => d.setAttribute("aria-current", i === real ? "true" : "false"));
  };

  track.addEventListener("transitionend", () => {
    if (pos === 0) pos = slides.length;
    else if (pos === slides.length + 1) pos = 1;
    else return;
    place(false);
  });

  hero.querySelector(".prev").addEventListener("click", () => show(pos - 1));
  hero.querySelector(".next").addEventListener("click", () => show(pos + 1));
  dots.forEach((d, i) => d.addEventListener("click", () => show(i + 1)));

  // Swipe. A drag of more than a sixth of the width turns the page; a drag of
  // any size cancels the tap, so swiping never opens a film by accident.
  let startX = null;
  let dx = 0;
  track.addEventListener("pointerdown", (e) => {
    if (e.pointerType === "mouse" && e.button !== 0) return;
    startX = e.clientX;
    dx = 0;
  });
  track.addEventListener("pointermove", (e) => {
    if (startX === null) return;
    dx = e.clientX - startX;
    if (Math.abs(dx) > 6) place(false, dx);
  });
  const release = () => {
    if (startX === null) return;
    startX = null;
    if (Math.abs(dx) > hero.clientWidth / 6) show(pos + (dx < 0 ? 1 : -1));
    else show(pos);
  };
  track.addEventListener("pointerup", release);
  track.addEventListener("pointercancel", release);
  track.addEventListener("pointerleave", release);
  track.addEventListener("click", (e) => {
    if (Math.abs(dx) > 6) e.preventDefault();
  });
  track.addEventListener("dragstart", (e) => e.preventDefault());

  // Left and right arrow keys move between highlights when the hero has focus.
  hero.addEventListener("keydown", (e) => {
    if (e.key === "ArrowLeft") show(pos - 1);
    else if (e.key === "ArrowRight") show(pos + 1);
  });

  show(1);
  place(false);
  addEventListener("resize", () => place(false));
})();
