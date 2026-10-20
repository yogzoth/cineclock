// Tests for email alerts, run with: node relay/alerts.test.mjs
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { handleAccount } from "./account.js";
import { findAlerts, renderEmail, runAlerts } from "./alerts.js";

function fakeD1() {
  const sqlite = new DatabaseSync(":memory:");
  const statement = (sql, args = []) => ({
    bind: (...values) => statement(sql, values),
    first: async () => sqlite.prepare(sql).get(...args) ?? null,
    all: async () => ({ results: sqlite.prepare(sql).all(...args) }),
    run: async () => sqlite.prepare(sql).run(...args),
    sql,
    args,
  });
  return {
    prepare: (sql) => statement(sql),
    batch: async (statements) => {
      for (const s of statements) sqlite.prepare(s.sql).run(...s.args);
    },
    sqlite,
  };
}

// Monday Oct 19 2026, 9:05 am in Vancouver.
const NOW = Date.parse("2026-10-19T16:05:00Z");
const hours = (h) => new Date(NOW + h * 3600e3).toISOString();
const SAVED_AT = Date.parse("2026-10-18T12:00:00Z");

const show = (at, seen, extra = {}) => ({ at, venue: "The Rio", room: null, url: "https://rio/tix", status: null, seen, ...extra });
const FEED = [
  // Final showing tomorrow evening (Tue Oct 20, 7 pm).
  { slug: "jaws-1975", title: "Jaws", year: 1975, imdb: "tt0073195", poster: null,
    screenings: [show(hours(-30), hours(-100)), show(hours(34), hours(-100))] },
  // Saved before these dates were added: two new screenings.
  { slug: "alien-1979", title: "Alien", year: 1979, imdb: "tt0078748", poster: "https://img/alien.jpg",
    screenings: [show(hours(80), hours(-48)), show(hours(120), hours(-2)), show(hours(150), hours(-1))] },
  // On the IMDb watchlist only.
  { slug: "heat-1995", title: "Heat", year: 1995, imdb: "tt0113277", poster: null,
    screenings: [show(hours(200), hours(-1))] },
  // Not saved at all.
  { slug: "cure-1997", title: "Cure", year: 1997, imdb: null, poster: null, screenings: [show(hours(34), hours(-1))] },
];

// What one user should hear about.
{
  const settings = { enabled: 1, added: 1, last: 1, imdb: 0, since: "2026-10-01T00:00:00Z" };
  const saved = new Map([["jaws-1975", SAVED_AT], ["alien-1979", SAVED_AT]]);
  const found = findAlerts({ settings, feed: FEED, saved, imdb: ["tt0113277"], sent: new Set(), now: NOW });
  assert.deepEqual(found.last.map((a) => a.film.slug), ["jaws-1975"]);
  assert.deepEqual(found.added.map((a) => [a.film.slug, a.screenings.length]), [["alien-1979", 2]]);

  // With the IMDb box ticked, Heat counts, measured from when alerts were turned on.
  const withImdb = findAlerts({ settings: { ...settings, imdb: 1, since: hours(-5) }, feed: FEED, saved, imdb: ["tt0113277"], sent: new Set(), now: NOW });
  assert.ok(withImdb.added.some((a) => a.film.slug === "heat-1995"));

  // Already sent: nothing again.
  const sent = new Set([`last|${found.last[0].key}`, ...found.added[0].keys.map((k) => `added|${k}`)]);
  const again = findAlerts({ settings, feed: FEED, saved, imdb: [], sent, now: NOW });
  assert.equal(again.last.length + again.added.length, 0);

  // Each kind can be switched off.
  const lastOnly = findAlerts({ settings: { ...settings, added: 0 }, feed: FEED, saved, imdb: [], sent: new Set(), now: NOW });
  assert.equal(lastOnly.added.length, 0);
  assert.equal(lastOnly.last.length, 1);

  const email = renderEmail(found, "https://api.cineclock.ca/alerts/off?u=1&t=x");
  assert.match(email.subject, /2 saved films have news/);
  assert.match(email.html, /Last chance/);
  assert.match(email.html, /Final showing Tue, Oct 20, 7:05 pm at The Rio/);
  assert.match(email.html, /New screenings/);
  assert.match(email.html, /alerts\/off\?u=1&amp;t=x/);
  assert.match(email.text, /Turn off alerts: https:\/\/api\.cineclock\.ca/);
}

// The whole thing: settings through the API, the cron run, one email, no repeats, unsubscribe.
{
  const env = { DB: fakeD1(), RESEND_API_KEY: "re_test" };
  const emails = [];
  const fetcher = async (url, init) => {
    if (String(url).endsWith("/alerts.json")) return new Response(JSON.stringify(FEED));
    if (String(url).startsWith("https://api.resend.com/")) {
      emails.push(JSON.parse(init.body));
      return new Response(JSON.stringify({ id: "1" }));
    }
    throw new Error(`unexpected fetch ${url}`);
  };
  const call = (method, path, body, cookie, now = NOW) =>
    handleAccount(
      new Request(`https://api.cineclock.ca${path}`, {
        method,
        headers: { Origin: "https://cineclock.ca", "Content-Type": "application/json", ...(cookie ? { Cookie: cookie } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      }),
      env,
      { now, fetcher },
    );

  // A signed-in user, made directly (sign-in itself is covered in account.test.mjs).
  await call("GET", "/me");
  const sqlite = env.DB.sqlite;
  sqlite.prepare("INSERT INTO users (id, google_sub, email, created) VALUES (1, 'g1', 'jo@example.com', ?)").run(hours(-500));
  const hash = await crypto.subtle.digest("SHA-256", new TextEncoder().encode("session-one-two-three"));
  const hashed = Buffer.from(hash).toString("base64url");
  sqlite.prepare("INSERT INTO sessions VALUES (?, 1, ?)").run(hashed, hours(1000));
  const cookie = "cc_session=session-one-two-three";
  for (const slug of ["jaws-1975", "alien-1979"]) {
    sqlite.prepare("INSERT INTO saved VALUES (1, 'film', ?, ?)").run(slug, new Date(SAVED_AT).toISOString());
  }

  // Off by default.
  let me = await (await call("GET", "/me", undefined, cookie)).json();
  assert.deepEqual(me.alerts, { enabled: false, added: true, last: true, imdb: true, digest: false, newsletter: false });
  assert.equal(me.email_ready, true);
  assert.deepEqual(await runAlerts(env, { now: NOW, fetcher }), { checked: 0, sent: 0 });

  // Turned on two days ago.
  me = await (await call("PUT", "/me/alerts", { enabled: true }, cookie, NOW - 48 * 3600e3)).json();
  assert.equal(me.alerts.enabled, true);
  assert.equal((await call("PUT", "/me/alerts", { enabled: "yes" }, cookie)).status, 400);

  const done = await runAlerts(env, { now: NOW, fetcher });
  assert.deepEqual(done, { checked: 1, sent: 1 });
  assert.equal(emails.length, 1);
  assert.deepEqual(emails[0].to, ["jo@example.com"]);
  assert.match(emails[0].headers["List-Unsubscribe"], /alerts\/off\?u=1&t=/);

  // Same day: already checked. Next day: nothing new, so no email.
  assert.deepEqual(await runAlerts(env, { now: NOW + 3600e3, fetcher }), { checked: 0, sent: 0 });
  assert.deepEqual(await runAlerts(env, { now: NOW + 24 * 3600e3, fetcher }), { checked: 1, sent: 0 });
  assert.equal(emails.length, 1);

  // A test send works any time and doesn't count as sent.
  const test = await call("POST", "/me/alerts/test", {}, cookie);
  assert.equal(test.status, 200);
  assert.match(emails[1].subject, /^Test/);

  // The link in the email only asks; mail scanners follow links, so a GET
  // mustn't turn anything off.
  const token = sqlite.prepare("SELECT token FROM alert_settings WHERE user_id = 1").get().token;
  const ask = await handleAccount(new Request(`https://api.cineclock.ca/alerts/off?u=1&t=${token}`), env, { now: NOW, fetcher });
  assert.match(await ask.text(), /<form method="post">/);
  assert.equal(sqlite.prepare("SELECT enabled FROM alert_settings WHERE user_id = 1").get().enabled, 1);

  // One-tap unsubscribe, with no Origin (a mail app's button).
  const bad = await handleAccount(new Request("https://api.cineclock.ca/alerts/off?u=1&t=wrongwrongwrongwrongwrong", { method: "POST" }), env, { now: NOW, fetcher });
  assert.match(await bad.text(), /doesn't match/);
  const off = await handleAccount(new Request(`https://api.cineclock.ca/alerts/off?u=1&t=${token}`, { method: "POST" }), env, { now: NOW, fetcher });
  assert.match(await off.text(), /Email alerts are off/);
  me = await (await call("GET", "/me", undefined, cookie)).json();
  assert.equal(me.alerts.enabled, false);

  // Deleting the account removes alert settings and history too.
  await call("DELETE", "/me", undefined, cookie);
  for (const table of ["alert_settings", "alerts_sent"]) {
    assert.equal(sqlite.prepare(`SELECT COUNT(*) AS n FROM ${table}`).get().n, 0, table);
  }
}

// Without an email key, alerts can be set up but the page is told they aren't live.
{
  const env = { DB: fakeD1() };
  const me = await handleAccount(new Request("https://api.cineclock.ca/me", { headers: { Origin: "https://cineclock.ca" } }), env);
  assert.equal(me.status, 401);
}

console.log("alert tests passed");

// Two runs at once email a user once; a failed send leaves them for the next run.
{
  let failing = true;
  const emails = [];
  const fetcher = async (url, init) => {
    if (String(url).endsWith("/alerts.json")) return new Response(JSON.stringify(FEED));
    if (failing) return new Response("down", { status: 500 });
    emails.push(JSON.parse(init.body));
    return new Response("{}");
  };
  const env = { DB: fakeD1(), RESEND_API_KEY: "re_test" };
  await handleAccount(new Request("https://api.cineclock.ca/me", { headers: { Origin: "https://cineclock.ca" } }), env);
  const sqlite = env.DB.sqlite;
  sqlite.prepare("INSERT INTO users (id, google_sub, email, created) VALUES (1, 'g1', 'jo@example.com', 'x')").run();
  sqlite.prepare("INSERT INTO saved VALUES (1, 'film', 'jaws-1975', ?)").run(new Date(SAVED_AT).toISOString());
  sqlite
    .prepare("INSERT INTO alert_settings (user_id, enabled, added, last, imdb, since, token) VALUES (1, 1, 1, 1, 0, ?, 'tok-tok-tok-tok-tok-tok')")
    .run(new Date(SAVED_AT).toISOString());

  assert.deepEqual(await runAlerts(env, { now: NOW, fetcher }), { checked: 1, sent: 0 });
  assert.equal(sqlite.prepare("SELECT checked FROM alert_settings").get().checked, null);

  failing = false;
  await Promise.all([runAlerts(env, { now: NOW, fetcher }), runAlerts(env, { now: NOW, fetcher })]);
  assert.equal(emails.length, 1);
  console.log("overlap tests passed");
}

