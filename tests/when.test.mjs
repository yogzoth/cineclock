// Checks static/when.js reads dates the same way the Python build does.
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const source = readFileSync(new URL("../src/cineclock/static/when.js", import.meta.url), "utf8");
const window = {};
const document = { querySelectorAll: () => [], addEventListener: () => {} };
new Function("window", "document", source)(window, document);
const text = window.cineclockWhen.text;

const today = "2026-10-19";
const cases = [
  ["2026-10-19T16:00", "card", "Today, 4:00 pm"],
  ["2026-10-20T00:05", "card", "Tomorrow, 12:05 am"],
  ["2026-10-18T21:30", "card", "Oct 18, 9:30 pm"],
  ["2026-11-02T12:00", "card", "Nov 2, 12:00 pm"],
  ["2026-10-24T20:15", "card", "Saturday 24, 8:15 pm"],
  ["2026-10-26T20:15", "card", "Oct 26, 8:15 pm"],
  ["2026-10-19", "ends", "Ends today"],
  ["2026-10-20", "ends", "Ends tomorrow"],
  ["2026-10-21", "ends", "Ends Wed 21"],
  ["2026-10-19", "day", "Today, Oct 19"],
  ["2026-10-22", "day", "Thursday, Oct 22"],
  ["2026-10-20", "short", "Tomorrow"],
  ["2026-10-23", "short", "Fri 23"],
  ["2026-11-01", "short", "Sun Nov 1"],
  // Across a year end
  ["2027-01-01T19:00", "card", "Tomorrow, 7:00 pm", "2026-12-31"],
];
for (const [value, fmt, expected, on = today] of cases) {
  assert.equal(text(value, fmt, on), expected, `${value} ${fmt}`);
}
console.log(`when.js: ${cases.length} passed`);
