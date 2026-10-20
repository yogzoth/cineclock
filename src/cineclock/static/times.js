// Showtime rows with more than two times show the first two and a +N button
// that opens the rest. Without JavaScript every time shows.
(() => {
  for (const button of document.querySelectorAll(".time-more")) {
    const times = button.parentElement;
    times.classList.add("is-folded");
    button.hidden = false;
    button.addEventListener("click", () => {
      times.classList.remove("is-folded");
      button.remove();
      times.querySelector(".time:nth-of-type(3)")?.focus();
    });
  }
})();
