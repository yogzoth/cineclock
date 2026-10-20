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

// The day comes from the build's fixed zone (data-zone), so a browser whose
// rules still move Vancouver to UTC-8 in November can't call 11:30 pm tomorrow.
{
  const zoned = {};
  const doc = { querySelectorAll: () => [], addEventListener: () => {}, body: { dataset: { zone: "Etc/GMT+7" } } };
  new Function("window", "document", source)(zoned, doc);
  const lateNov = Date.parse("2026-11-15T06:30:00Z"); // 11:30 pm Nov 14 at UTC-7
  assert.equal(zoned.cineclockWhen.todayIso(new Date(lateNov)), "2026-11-14");
}
console.log(`when.js: ${cases.length} passed`);

// Half past midnight on Nov 15, 2026 in Vancouver (UTC-7 all year now). With the
// build's fixed zone, it's the 15th even where the browser still thinks
// Vancouver falls back to UTC-8 in November.
{
  const win = {};
  const doc = { querySelectorAll: () => [], addEventListener: () => {}, body: { dataset: { zone: "Etc/GMT+7" } } };
  const RealDate = Date;
  globalThis.Date = class extends RealDate {
    constructor(...args) {
      super(...(args.length ? args : ["2026-11-15T07:30:00Z"]));
    }
  };
  new Function("window", "document", source)(win, doc);
  assert.equal(win.cineclockWhen.todayIso(), "2026-11-15");
  globalThis.Date = RealDate;
  console.log("when.js: fixed zone passed");
}
