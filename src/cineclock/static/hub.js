// Home page hub: the middle tile shows one of the festivals on now or soon (or
// special screenings), a different one on each visit. The build lists them all,
// with only the biggest showing, so the page works without this too.
(() => {
  const picks = [...document.querySelectorAll("[data-hub-pick]")];
  if (picks.length < 2) return;
  const chosen = picks[Math.floor(Math.random() * picks.length)];
  for (const tile of picks) tile.hidden = tile !== chosen;
})();
