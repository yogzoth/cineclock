// The date picker and the strip of day chips beside it.
(() => {
  const pop = document.querySelector(".cal-pop");
  if (pop) {
    // Close the picker after choosing a day, or when tapping anywhere else.
    pop.addEventListener("click", (e) => {
      if (e.target.closest("a")) pop.open = false;
    });
    document.addEventListener("click", (e) => {
      if (!pop.contains(e.target)) pop.open = false;
    });
  }

  // The strip marks the day you're looking at, and keeps that chip in view as
  // you scroll down the listings.
  const strip = document.querySelector(".date-strip");
  if (!strip) return;
  const chips = [...strip.querySelectorAll("a")];
  const days = chips.map((a) => document.querySelector(a.getAttribute("href")));
  const smooth = matchMedia("(prefers-reduced-motion: no-preference)").matches ? "smooth" : "auto";
  let current = -1;
  let held = 0;

  function mark(index) {
    if (index === current || index < 0) return;
    current = index;
    chips.forEach((chip, i) => chip.toggleAttribute("aria-current", i === index));
    chips[index].setAttribute("aria-current", "true");
    const chip = chips[index];
    const left = chip.offsetLeft - strip.offsetLeft;
    if (left < strip.scrollLeft || left + chip.offsetWidth > strip.scrollLeft + strip.clientWidth) {
      strip.scrollTo({ left: Math.max(0, left - 16), behavior: smooth });
    }
  }

  function onScroll() {
    // The last day whose heading has reached the sticky chips.
    if (Date.now() < held) return;
    const line = strip.getBoundingClientRect().bottom + 64;
    let index = 0;
    days.forEach((day, i) => {
      if (day && !day.hidden && day.getBoundingClientRect().top <= line) index = i;
    });
    mark(index);
  }

  let queued = false;
  addEventListener(
    "scroll",
    () => {
      if (queued) return;
      queued = true;
      requestAnimationFrame(() => {
        queued = false;
        onScroll();
      });
    },
    { passive: true },
  );
  // A tapped day stays lit while the page scrolls to it, even near the bottom
  // where its heading can't reach the top.
  chips.forEach((chip, i) =>
    chip.addEventListener("click", () => {
      mark(i);
      held = Date.now() + 1000;
    }),
  );
  onScroll();
})();
