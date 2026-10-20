// Email alerts for signed-in users who turn them on.
//
// A few times each morning the Worker's cron (wrangler.toml) runs runAlerts. It
// reads alerts.json, the build's list of every upcoming screening with when
// cineclock first saw it, and for each user not yet checked today works out:
//
//   - new screenings of a film they saved, first seen after they saved it
//   - last chance: a saved film whose final showing is tomorrow
//
// IMDb watchlist films count as saved when the user ticks that box. Whatever
// is found goes out as one email through Resend; alerts_sent remembers each
// screening and last showing already mentioned, so nothing is sent twice.

const FEED_URL = "https://cineclock.ca/alerts.json";
const SITE = "https://cineclock.ca/";
const API = "https://api.cineclock.ca";
const FROM = "cineclock <alerts@cineclock.ca>";
// B.C. has been on UTC-7 all year since March 2026. A browser's or Worker's own
// time zone data may be older and still switch to UTC-8 in November, so the
// offset is fixed here (Etc/GMT+7 means UTC-7).
const ZONE = "Etc/GMT+7";
// D1 allows 50 queries per run on the free plan; each user takes about five.
const USERS_PER_RUN = 6;
const DATES_PER_FILM = 4;

export const ALERT_SCHEMA = [
  `CREATE TABLE IF NOT EXISTS alert_settings (
    user_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 0,
    added INTEGER NOT NULL DEFAULT 1,
    last INTEGER NOT NULL DEFAULT 1,
    imdb INTEGER NOT NULL DEFAULT 0,
    since TEXT,
    token TEXT,
    checked TEXT
  )`,
  `CREATE TABLE IF NOT EXISTS alerts_sent (
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    sent TEXT NOT NULL,
    PRIMARY KEY (user_id, kind, key)
  )`,
];

export const DEFAULT_ALERTS = {
  enabled: false,
  added: true,
  last: true,
  imdb: true,
  digest: false,
  newsletter: false,
};

const localDate = (date) =>
  new Intl.DateTimeFormat("en-CA", { timeZone: ZONE, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);

const dayAndTime = (iso) =>
  new Intl.DateTimeFormat("en-CA", {
    timeZone: ZONE,
    weekday: "short",
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
    .format(new Date(iso))
    .replace(/\./g, "")
    .replace(" a m", " am")
    .replace(" p m", " pm");

const escape = (text) =>
  String(text ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

// What one user should hear about now. `saved` maps film slug to when it was
// saved; `sent` is a Set of "kind|key" already emailed.
export function findAlerts({ settings, feed, saved, imdb, sent, now, ignoreSent = false }) {
  const tomorrow = localDate(new Date(now + 864e5));
  const since = settings.since ? Date.parse(settings.since) : 0;
  const watched = new Set(imdb);
  const last = [];
  const added = [];
  for (const film of feed) {
    let savedAt = saved.get(film.slug);
    if (savedAt === undefined && settings.imdb && film.imdb && watched.has(film.imdb)) savedAt = since;
    if (savedAt === undefined) continue;
    const upcoming = film.screenings.filter((s) => Date.parse(s.at) > now);
    if (!upcoming.length) continue;
    const final = upcoming.at(-1);
    const lastKey = `${film.slug}|${final.at}`;
    if (settings.last && localDate(new Date(final.at)) === tomorrow && (ignoreSent || !sent.has(`last|${lastKey}`))) {
      last.push({ film, screening: final, key: lastKey });
      continue;
    }
    if (!settings.added) continue;
    const baseline = Math.max(savedAt, since);
    const fresh = upcoming.filter(
      (s) => s.seen && Date.parse(s.seen) > baseline && (ignoreSent || !sent.has(`added|${film.slug}|${s.at}`)),
    );
    if (fresh.length) added.push({ film, screenings: fresh, keys: fresh.map((s) => `${film.slug}|${s.at}`) });
  }
  return { last, added };
}

function filmBlock(film, lines, button) {
  const poster = film.poster
    ? `<td width="64" valign="top" style="padding:0 14px 0 0"><img src="${escape(film.poster)}" width="64" alt="" style="display:block;border-radius:4px"></td>`
    : "";
  return `<table role="presentation" cellpadding="0" cellspacing="0" style="margin:0 0 20px"><tr>${poster}
    <td valign="top" style="font:15px/1.45 Helvetica,Arial,sans-serif;color:#1c2228">
      <a href="${SITE}films/${escape(film.slug)}/" style="color:#14181c;font-weight:700;font-size:16px;text-decoration:none">${escape(film.title)}${film.year ? ` <span style="color:#7a8491;font-weight:400">${film.year}</span>` : ""}</a>
      ${lines}
      ${button}
    </td></tr></table>`;
}

const ticketButton = (s) =>
  s.status
    ? `<div style="margin-top:6px;color:#7a8491;font-size:13px">${escape(s.status)}</div>`
    : `<a href="${escape(s.url)}" style="display:inline-block;margin-top:8px;padding:7px 14px;border-radius:999px;background:#8830df;color:#fff;font:600 13px Helvetica,Arial,sans-serif;text-decoration:none">Tickets</a>`;

const where = (s) => `${escape(s.venue)}${s.room ? `, ${escape(s.room)}` : ""}`;

export function renderEmail({ last, added }, unsubscribeUrl, test = false) {
  const sections = [];
  const text = [];
  if (last.length) {
    sections.push(`<h2 style="margin:0 0 14px;font:700 18px Helvetica,Arial,sans-serif;color:#14181c">Last chance</h2>`);
    text.push("LAST CHANCE");
    for (const { film, screening } of last) {
      sections.push(
        filmBlock(
          film,
          `<div style="color:#3a4350">Final showing ${dayAndTime(screening.at)} at ${where(screening)}</div>`,
          ticketButton(screening),
        ),
      );
      text.push(`${film.title}: final showing ${dayAndTime(screening.at)} at ${screening.venue}. ${screening.url}`);
    }
  }
  if (added.length) {
    sections.push(`<h2 style="margin:${last.length ? "28px" : "0"} 0 14px;font:700 18px Helvetica,Arial,sans-serif;color:#14181c">New screenings</h2>`);
    text.push("", "NEW SCREENINGS");
    for (const { film, screenings } of added) {
      const shown = screenings.slice(0, DATES_PER_FILM);
      const more = screenings.length - shown.length;
      const lines = shown.map((s) => `<div style="color:#3a4350">${dayAndTime(s.at)} at ${where(s)}</div>`).join("");
      const extra = more > 0 ? `<div style="color:#7a8491;font-size:13px">and ${more} more</div>` : "";
      sections.push(filmBlock(film, lines + extra, ticketButton(shown[0])));
      text.push(`${film.title}: ${shown.map((s) => `${dayAndTime(s.at)} at ${s.venue}`).join("; ")}${more > 0 ? ` and ${more} more` : ""}`);
    }
  }
  const count = last.length + added.length;
  const subject = test
    ? "Test: your cineclock alerts"
    : last.length && !added.length
      ? `Last chance: ${last.map((a) => a.film.title).join(", ")}`
      : count === 1
        ? `New screenings: ${added[0].film.title}`
        : `${count} saved films have news`;
  const html = `<!doctype html><html><body style="margin:0;padding:0;background:#f3f4f6">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f3f4f6"><tr><td align="center" style="padding:24px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:560px;background:#ffffff;border-radius:12px">
<tr><td style="padding:22px 24px 6px;font:800 22px Helvetica,Arial,sans-serif;letter-spacing:-0.5px"><span style="color:#14181c">cine</span><span style="color:#8830df">clock</span></td></tr>
${test ? `<tr><td style="padding:6px 24px 0;font:14px Helvetica,Arial,sans-serif;color:#7a8491">This is a test of your alerts, showing what's on for your saved films right now.</td></tr>` : ""}
<tr><td style="padding:18px 24px 8px">${sections.join("")}</td></tr>
<tr><td style="padding:16px 24px 22px;border-top:1px solid #e5e7eb;font:12px/1.5 Helvetica,Arial,sans-serif;color:#7a8491">
You get this because you turned on email alerts for films you saved on cineclock. Showtimes come from each theatre's website; check there before you go.<br>
<a href="${SITE}me/" style="color:#7a8491">Alert settings</a> &nbsp;·&nbsp; <a href="${escape(unsubscribeUrl)}" style="color:#7a8491">Turn off alerts</a>
</td></tr></table></td></tr></table></body></html>`;
  text.push("", `Alert settings: ${SITE}me/`, `Turn off alerts: ${unsubscribeUrl}`);
  return { subject, html, text: text.join("\n") };
}

export async function sendEmail(env, to, message, unsubscribeUrl, fetcher = fetch) {
  if (!env.RESEND_API_KEY) throw new Error("email isn't set up yet (no RESEND_API_KEY)");
  const response = await fetcher("https://api.resend.com/emails", {
    method: "POST",
    headers: { Authorization: `Bearer ${env.RESEND_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      from: FROM,
      to: [to],
      subject: message.subject,
      html: message.html,
      text: message.text,
      headers: {
        "List-Unsubscribe": `<${unsubscribeUrl}>`,
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
      },
    }),
  });
  if (!response.ok) throw new Error(`Resend answered ${response.status}: ${(await response.text()).slice(0, 200)}`);
}

export const unsubscribeUrl = (userId, token) => `${API}/alerts/off?u=${userId}&t=${token}`;

async function userContext(db, userId) {
  const saved = await db
    .prepare("SELECT ref, created FROM saved WHERE user_id = ? AND kind = 'film'")
    .bind(userId)
    .all();
  const imdb = await db.prepare("SELECT imdb_id FROM imdb WHERE user_id = ?").bind(userId).all();
  return {
    saved: new Map(saved.results.map((r) => [r.ref, Date.parse(r.created)])),
    imdb: imdb.results.map((r) => r.imdb_id),
  };
}

export async function loadFeed(fetcher = fetch) {
  const response = await fetcher(FEED_URL, { cf: { cacheTtl: 300 } });
  if (!response.ok) throw new Error(`alerts.json: ${response.status}`);
  return response.json();
}

// The cron job. Returns what it did, for the logs and the tests.
export async function runAlerts(env, { now = Date.now(), fetcher = fetch } = {}) {
  const db = env.DB;
  const today = localDate(new Date(now));
  const due = await db
    .prepare(
      `SELECT a.*, u.email FROM alert_settings a JOIN users u ON u.id = a.user_id
       WHERE a.enabled = 1 AND u.email IS NOT NULL AND (a.checked IS NULL OR a.checked < ?)
       ORDER BY a.user_id LIMIT ?`,
    )
    .bind(today, USERS_PER_RUN)
    .all();
  if (!due.results.length) return { checked: 0, sent: 0 };
  const feed = await loadFeed(fetcher);
  const stamp = new Date(now).toISOString();
  let sent = 0;
  for (const settings of due.results) {
    // Claim the user for today first, so two runs that overlap can't both email
    // them. A failed send hands the claim back for the next run.
    const claim = await db
      .prepare("UPDATE alert_settings SET checked = ? WHERE user_id = ? AND (checked IS NULL OR checked < ?)")
      .bind(today, settings.user_id, today)
      .run();
    if (!(claim.meta?.changes ?? claim.changes)) continue;
    try {
      const { saved, imdb } = await userContext(db, settings.user_id);
      const already = await db.prepare("SELECT kind, key FROM alerts_sent WHERE user_id = ?").bind(settings.user_id).all();
      const sentKeys = new Set(already.results.map((r) => `${r.kind}|${r.key}`));
      const found = findAlerts({ settings, feed, saved, imdb, sent: sentKeys, now });
      if (found.last.length || found.added.length) {
        const unsub = unsubscribeUrl(settings.user_id, settings.token);
        await sendEmail(env, settings.email, renderEmail(found, unsub), unsub, fetcher);
        const rows = [
          ...found.last.map((a) => ["last", a.key]),
          ...found.added.flatMap((a) => a.keys.map((k) => ["added", k])),
        ];
        await db
          .prepare(
            "INSERT OR IGNORE INTO alerts_sent SELECT ?, json_extract(value, '$[0]'), json_extract(value, '$[1]'), ? FROM json_each(?)",
          )
          .bind(settings.user_id, stamp, JSON.stringify(rows))
          .run();
        sent += 1;
      }
    } catch (error) {
      // One bad send shouldn't stop the others; this user is retried next run.
      console.error(`alerts for user ${settings.user_id}:`, error);
      await db
        .prepare("UPDATE alert_settings SET checked = ? WHERE user_id = ?")
        .bind(settings.checked ?? null, settings.user_id)
        .run()
        .catch(() => {});
    }
  }
  return { checked: due.results.length, sent };
}

// "Send me a test": everything that applies right now, ignoring what was sent.
// With nothing new, it shows each saved film's next showing so the email isn't empty.
export async function sendTest(env, user, settings, { now = Date.now(), fetcher = fetch } = {}) {
  const feed = await loadFeed(fetcher);
  const { saved, imdb } = await userContext(env.DB, user.id);
  // Treat everything as just saved and nothing as sent, so every showing counts.
  const everySaved = new Map([...saved].map(([slug]) => [slug, 0]));
  const found = findAlerts({
    settings: { ...settings, since: null, added: true, last: true },
    feed,
    saved: everySaved,
    imdb,
    sent: new Set(),
    now,
    ignoreSent: true,
  });
  if (!found.last.length && !found.added.length) {
    throw new Error("none of your saved films are playing right now, so there's nothing to show yet");
  }
  const unsub = unsubscribeUrl(user.id, settings.token);
  await sendEmail(env, user.email, renderEmail(found, unsub, true), unsub, fetcher);
}

const page = (title, body) =>
  new Response(
    `<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>${title}</title>
<body style="margin:0;background:#14181c;color:#dde5ed;font:16px/1.5 Helvetica,Arial,sans-serif;display:grid;place-items:center;min-height:100vh">
<div style="max-width:24rem;padding:24px;text-align:center"><div style="font:800 24px Helvetica,Arial,sans-serif;margin-bottom:16px">cine<span style="color:#8830df">clock</span></div>
<p>${body}</p><p><a href="${SITE}me/" style="color:#b98af0">Your cineclock</a></p></div></body>`,
    { status: 200, headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" } },
  );

// Unsubscribe. The mail app's one-click button POSTs (RFC 8058). The link in the
// email opens a page with a button that POSTs, because mail scanners follow
// every link in a message, and a GET that unsubscribed would turn people's
// alerts off without them ever clicking.
export async function unsubscribe(request, db) {
  const url = new URL(request.url);
  const userId = Number(url.searchParams.get("u"));
  const token = url.searchParams.get("t") || "";
  const row = userId && token.length >= 20
    ? await db.prepare("SELECT token FROM alert_settings WHERE user_id = ?").bind(userId).first()
    : null;
  if (!row || row.token !== token) return page("Link expired", "That link doesn't match an account. You can turn alerts off on Your cineclock.");
  if (request.method !== "POST") {
    return page(
      "Turn off alerts?",
      `Stop cineclock's emails to this address?</p><form method="post"><button type="submit" style="font:600 16px Helvetica,Arial,sans-serif;padding:10px 20px;border:0;border-radius:999px;background:#8830df;color:#fff;cursor:pointer">Turn off emails</button></form><p>`,
    );
  }
  await db.prepare("UPDATE alert_settings SET enabled = 0 WHERE user_id = ?").bind(userId).run();
  return page("Alerts off", "Email alerts are off. Your saved films are still there, and you can turn alerts back on any time.");
}
