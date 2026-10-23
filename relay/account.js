// cineclock accounts: Google sign-in, saved films and theatres, settings.
//
// The site is plain files, so this part of the Worker holds everything that
// belongs to a signed-in person, in a D1 (SQLite) database. Sign-in is Google's
// button: the browser gets a signed ID token from Google and posts it here; the
// Worker checks Google's signature, finds or creates the user, and sets a
// session cookie. Only a hash of the session id is stored.
//
//   POST   /auth/google   { credential }   sign in
//   POST   /auth/signout                   sign out
//   GET    /me                             everything saved for this user
//   PUT    /me/saved      { kind, ref, saved }
//   POST   /me/saved      { items: [{ kind, ref }] }   merge saves made signed out
//   PUT    /me/settings   { rating_source?, letterboxd?, imdb_user?, avatar? }
//   PUT    /me/imdb       { ids: ["tt0000001", ...] }  replace the IMDb watchlist
//   PUT    /me/alerts     { enabled?, added?, last?, imdb? }   email alert settings
//   POST   /me/alerts/test                 email a test alert now
//   DELETE /me                             delete the account and everything in it
//   GET|POST /alerts/off?u=&t=             unsubscribe: GET asks, POST does it

export const GOOGLE_CLIENT_ID =
  "358607256767-mkorjtl09q7ii10nuhfo1fesjfcvgnnv.apps.googleusercontent.com";
const GOOGLE_KEYS_URL = "https://www.googleapis.com/oauth2/v3/certs";
const GOOGLE_ISSUERS = ["accounts.google.com", "https://accounts.google.com"];

export const SITE_ORIGINS = [
  "https://cineclock.ca",
  "https://www.cineclock.ca",
  "http://localhost:8000",
  "http://127.0.0.1:8000",
];

const COOKIE = "cc_session";
const SESSION_DAYS = 180;
export const RATING_SOURCES = ["letterboxd", "imdb", "rt", "metacritic"];
const KINDS = ["film", "theatre"];
const SLUG = /^[a-z0-9][a-z0-9-]{0,119}$/;
const IMDB_ID = /^tt\d{5,10}$/;
const LETTERBOXD_USER = /^[A-Za-z0-9_]{1,30}$/;
const IMDB_USER = /^(ur\d{4,12}|p\.[a-z0-9]{8,40})$/;
const MAX_IMDB = 10000;
const MAX_MERGE = 500;
// The biggest body anything here needs is a 10,000-film IMDb list, about 150 KB.
export const MAX_BODY = 512 * 1024;

import { ALERT_SCHEMA, DEFAULT_ALERTS, sendTest, unsubscribe } from "./alerts.js";

const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    google_sub TEXT NOT NULL UNIQUE,
    email TEXT,
    letterboxd TEXT,
    rating_source TEXT NOT NULL DEFAULT 'letterboxd',
    created TEXT NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS sessions (
    hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    expires TEXT NOT NULL
  )`,
  `CREATE TABLE IF NOT EXISTS saved (
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    created TEXT NOT NULL,
    PRIMARY KEY (user_id, kind, ref)
  )`,
  `CREATE TABLE IF NOT EXISTS imdb (
    user_id INTEGER NOT NULL,
    imdb_id TEXT NOT NULL,
    PRIMARY KEY (user_id, imdb_id)
  )`,
];

const schemaReady = new WeakMap();

export async function setup(db) {
  // Tables are created the first time this Worker instance touches the database.
  if (!schemaReady.has(db)) {
    const ready = db.batch([...SCHEMA, ...ALERT_SCHEMA].map((sql) => db.prepare(sql))).catch((error) => {
      schemaReady.delete(db);
      throw error;
    });
    schemaReady.set(db, ready.then(addColumns));
  }
  await schemaReady.get(db);

  // Columns added after the tables first shipped; already there is fine.
  async function addColumns() {
    for (const sql of [
      "ALTER TABLE users ADD COLUMN avatar TEXT",
      "ALTER TABLE users ADD COLUMN imdb_user TEXT",
      "ALTER TABLE alert_settings ADD COLUMN digest INTEGER NOT NULL DEFAULT 0",
      "ALTER TABLE alert_settings ADD COLUMN newsletter INTEGER NOT NULL DEFAULT 0",
    ]) {
      await db.prepare(sql).run().catch(() => {});
    }
  }
}

// Profile pictures a user can pick (static/avatars/<name>.jpg), or none.
export const AVATARS = ["popcorn", "candy", "carpet", "negative", "curtain"];

// Google's signing keys, kept for an hour.
let keyCache = { keys: null, until: 0 };

export async function googleKeys(fetcher = fetch, now = Date.now()) {
  if (keyCache.keys && now < keyCache.until) return keyCache.keys;
  const response = await fetcher(GOOGLE_KEYS_URL);
  if (!response.ok) throw new Error(`Google keys: ${response.status}`);
  const { keys } = await response.json();
  keyCache = { keys, until: now + 60 * 60 * 1000 };
  return keys;
}

export function fromBase64url(text) {
  const base64 = text.replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(base64 + "=".repeat((4 - (base64.length % 4)) % 4));
  return Uint8Array.from(binary, (c) => c.charCodeAt(0));
}

function toBase64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export const decodeJson = (part) => JSON.parse(new TextDecoder().decode(fromBase64url(part)));

// Returns the token's claims if Google signed it for cineclock and it hasn't
// expired; otherwise null.
// Checks a Google ID token. Returns { claims } when Google signed it for
// cineclock and it hasn't expired, or { reason } saying what was wrong.
export async function checkGoogleToken(token, keys, now = Date.now()) {
  const parts = String(token || "").split(".");
  if (parts.length !== 3) return { reason: "not a token" };
  let header, claims;
  try {
    header = decodeJson(parts[0]);
    claims = decodeJson(parts[1]);
  } catch {
    return { reason: "unreadable token" };
  }
  if (header.alg !== "RS256") return { reason: `algorithm ${header.alg}` };
  const jwk = keys.find((k) => k.kid === header.kid);
  if (!jwk) return { reason: "unknown Google key" };
  const key = await crypto.subtle.importKey(
    "jwk",
    jwk,
    { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" },
    false,
    ["verify"],
  );
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  const good = await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, fromBase64url(parts[2]), signed);
  if (!good) return { reason: "bad signature" };
  if (!GOOGLE_ISSUERS.includes(claims.iss)) return { reason: "wrong issuer" };
  if (claims.aud !== GOOGLE_CLIENT_ID) return { reason: "wrong client" };
  if (!claims.sub) return { reason: "no account id" };
  if (!(claims.exp * 1000 > now)) return { reason: "expired" };
  return { claims };
}

export async function verifyGoogleToken(token, keys, now = Date.now()) {
  return (await checkGoogleToken(token, keys, now)).claims || null;
}

async function sha256(text) {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return toBase64url(new Uint8Array(digest));
}

// The session comes from the cookie, or from an Authorization header when the
// site reached the Worker at its workers.dev address (a network that can't find
// api.cineclock.ca yet), where a cookie wouldn't be first-party.
function readCookie(request) {
  const bearer = (request.headers.get("Authorization") || "").match(/^Bearer ([A-Za-z0-9_-]{20,})$/);
  if (bearer) return bearer[1];
  const header = request.headers.get("Cookie") || "";
  const found = header.split(/;\s*/).find((c) => c.startsWith(`${COOKIE}=`));
  return found ? found.slice(COOKIE.length + 1) : null;
}

function sessionCookie(id, maxAge) {
  return `${COOKIE}=${id}; Path=/; HttpOnly; Secure; SameSite=Lax; Max-Age=${maxAge}`;
}

export function reply(body, status, origin, extra = {}) {
  const headers = { "Content-Type": "application/json", "Cache-Control": "no-store", Vary: "Origin", ...extra };
  if (SITE_ORIGINS.includes(origin)) {
    headers["Access-Control-Allow-Origin"] = origin;
    headers["Access-Control-Allow-Credentials"] = "true";
  }
  return new Response(body === null ? null : JSON.stringify(body), { status, headers });
}

export function preflight(origin) {
  return reply(null, 204, origin, {
    "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE",
    "Access-Control-Allow-Headers": "Content-Type, Authorization",
    "Access-Control-Max-Age": "86400",
  });
}

// The signed-in user for a request, for other parts of the Worker (imdb.js).
export async function sessionUser(db, request, now = Date.now()) {
  await setup(db);
  return currentUser(db, request, now);
}

async function currentUser(db, request, now) {
  const id = readCookie(request);
  if (!id) return null;
  return db
    .prepare(
      "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id WHERE sessions.hash = ? AND sessions.expires > ?",
    )
    .bind(await sha256(id), new Date(now).toISOString())
    .first();
}

async function everything(db, user) {
  const saved = { film: [], theatre: [] };
  const rows = await db
    .prepare("SELECT kind, ref FROM saved WHERE user_id = ? ORDER BY created, rowid")
    .bind(user.id)
    .all();
  for (const row of rows.results) saved[row.kind]?.push(row.ref);
  const imdb = await db.prepare("SELECT imdb_id FROM imdb WHERE user_id = ?").bind(user.id).all();
  const row = await alertSettings(db, user.id);
  return {
    email: user.email,
    letterboxd: user.letterboxd,
    imdb_user: user.imdb_user || null,
    rating_source: user.rating_source,
    avatar: user.avatar || null,
    saved,
    imdb: imdb.results.map((r) => r.imdb_id),
    alerts: row
      ? {
          enabled: Boolean(row.enabled),
          added: Boolean(row.added),
          last: Boolean(row.last),
          imdb: Boolean(row.imdb),
          digest: Boolean(row.digest),
          newsletter: Boolean(row.newsletter),
        }
      : DEFAULT_ALERTS,
    email_ready: emailReady,
  };
}

// Whether the Worker has an email key, so the page can say alerts aren't live yet.
let emailReady = false;

const alertSettings = (db, userId) =>
  db.prepare("SELECT * FROM alert_settings WHERE user_id = ?").bind(userId).first();

async function signIn(db, body, now, fetcher) {
  const { claims, reason } = await checkGoogleToken(body?.credential, await googleKeys(fetcher, now), now);
  if (!claims) return { reason };
  const stamp = new Date(now).toISOString();
  await db
    .prepare(
      "INSERT INTO users (google_sub, email, created) VALUES (?, ?, ?) ON CONFLICT (google_sub) DO UPDATE SET email = excluded.email",
    )
    // Only an address Google has verified gets alert emails.
    .bind(claims.sub, claims.email_verified === true ? claims.email || null : null, stamp)
    .run();
  const user = await db.prepare("SELECT * FROM users WHERE google_sub = ?").bind(claims.sub).first();
  const id = toBase64url(crypto.getRandomValues(new Uint8Array(32)));
  const expires = new Date(now + SESSION_DAYS * 864e5).toISOString();
  await db.batch([
    db.prepare("DELETE FROM sessions WHERE expires <= ?").bind(stamp),
    db.prepare("INSERT INTO sessions VALUES (?, ?, ?)").bind(await sha256(id), user.id, expires),
  ]);
  return { user, id };
}

const validItem = (item) => KINDS.includes(item?.kind) && SLUG.test(item?.ref || "");

export async function handleAccount(request, env, options = {}) {
  try {
    return await route(request, env, options);
  } catch (error) {
    // Without this, a crash answers with no CORS headers and the browser only
    // sees "failed to fetch". Say what broke instead.
    // The details stay in the Worker's log, not in the answer.
    console.error("account error:", error);
    const origin = request.headers.get("Origin") || "";
    return reply({ error: "server error" }, 500, origin);
  }
}

async function route(request, env, { now = Date.now(), fetcher = fetch } = {}) {
  const url = new URL(request.url);
  const origin = request.headers.get("Origin") || "";
  if (request.method === "OPTIONS") return preflight(origin);
  emailReady = Boolean(env.RESEND_API_KEY);
  // Mail apps post here with no Origin, so this comes before the origin check.
  if (url.pathname.replace(/\/+$/, "") === "/alerts/off") {
    await setup(env.DB);
    return unsubscribe(request, env.DB);
  }
  // Anything that changes data must come from the site itself.
  if (request.method !== "GET" && !SITE_ORIGINS.includes(origin)) {
    return reply({ error: "forbidden" }, 403, origin);
  }
  if (Number(request.headers.get("Content-Length") || 0) > MAX_BODY) {
    return reply({ error: "too large" }, 413, origin);
  }
  const db = env.DB;
  await setup(db);
  const path = url.pathname.replace(/\/+$/, "");
  let body = null;
  if (["POST", "PUT"].includes(request.method)) {
    try {
      body = await request.json();
    } catch {
      body = null;
    }
  }

  if (path === "/auth/google" && request.method === "POST") {
    const session = await signIn(db, body, now, fetcher);
    if (!session.id) {
      console.log("sign-in refused:", session.reason);
      return reply({ error: "sign-in failed", reason: session.reason }, 401, origin);
    }
    return reply({ ...(await everything(db, session.user)), token: session.id }, 200, origin, {
      "Set-Cookie": sessionCookie(session.id, SESSION_DAYS * 86400),
    });
  }

  if (path === "/auth/signout" && request.method === "POST") {
    const id = readCookie(request);
    if (id) await db.prepare("DELETE FROM sessions WHERE hash = ?").bind(await sha256(id)).run();
    return reply({ ok: true }, 200, origin, { "Set-Cookie": sessionCookie("", 0) });
  }

  const user = await currentUser(db, request, now);
  if (!user) return reply({ error: "signed out" }, 401, origin);
  const stamp = new Date(now).toISOString();

  if (path === "/me" && request.method === "GET") return reply(await everything(db, user), 200, origin);

  if (path === "/me" && request.method === "DELETE") {
    await db.batch(
      ["sessions", "saved", "imdb", "alert_settings", "alerts_sent"].map((table) =>
        db.prepare(`DELETE FROM ${table} WHERE user_id = ?`).bind(user.id),
      ).concat(db.prepare("DELETE FROM users WHERE id = ?").bind(user.id)),
    );
    return reply({ ok: true }, 200, origin, { "Set-Cookie": sessionCookie("", 0) });
  }

  if (path === "/me/saved" && request.method === "PUT") {
    if (!validItem(body) || typeof body.saved !== "boolean") return reply({ error: "bad request" }, 400, origin);
    const statement = body.saved
      ? db.prepare("INSERT OR IGNORE INTO saved VALUES (?, ?, ?, ?)").bind(user.id, body.kind, body.ref, stamp)
      : db.prepare("DELETE FROM saved WHERE user_id = ? AND kind = ? AND ref = ?").bind(user.id, body.kind, body.ref);
    await statement.run();
    return reply(await everything(db, user), 200, origin);
  }

  if (path === "/me/saved" && request.method === "POST") {
    const items = Array.isArray(body?.items) ? body.items.filter(validItem).slice(0, MAX_MERGE) : null;
    if (!items) return reply({ error: "bad request" }, 400, origin);
    // One statement for the lot: D1's free plan allows 50 queries per request.
    await db
      .prepare(
        "INSERT OR IGNORE INTO saved SELECT ?, json_extract(value, '$.kind'), json_extract(value, '$.ref'), ? FROM json_each(?)",
      )
      .bind(user.id, stamp, JSON.stringify(items.map(({ kind, ref }) => ({ kind, ref }))))
      .run();
    return reply(await everything(db, user), 200, origin);
  }

  if (path === "/me/settings" && request.method === "PUT") {
    const source = body?.rating_source ?? user.rating_source;
    const letterboxd = body?.letterboxd === undefined ? user.letterboxd : body.letterboxd || null;
    const avatar = body?.avatar === undefined ? user.avatar || null : body.avatar || null;
    const imdbUser = body?.imdb_user === undefined ? user.imdb_user || null : body.imdb_user || null;
    if (imdbUser !== null && !IMDB_USER.test(imdbUser)) return reply({ error: "bad request" }, 400, origin);
    if (!RATING_SOURCES.includes(source)) return reply({ error: "bad request" }, 400, origin);
    if (letterboxd !== null && !LETTERBOXD_USER.test(letterboxd)) return reply({ error: "bad request" }, 400, origin);
    if (avatar !== null && !AVATARS.includes(avatar)) return reply({ error: "bad request" }, 400, origin);
    await db
      .prepare("UPDATE users SET rating_source = ?, letterboxd = ?, avatar = ?, imdb_user = ? WHERE id = ?")
      .bind(source, letterboxd, avatar, imdbUser, user.id)
      .run();
    const updated = { ...user, rating_source: source, letterboxd, avatar, imdb_user: imdbUser };
    return reply(await everything(db, updated), 200, origin);
  }

  if (path === "/me/imdb" && request.method === "PUT") {
    const ids = Array.isArray(body?.ids) ? [...new Set(body.ids)].filter((id) => IMDB_ID.test(id)) : null;
    if (!ids || ids.length > MAX_IMDB) return reply({ error: "bad request" }, 400, origin);
    await db.batch([
      db.prepare("DELETE FROM imdb WHERE user_id = ?").bind(user.id),
      db.prepare("INSERT INTO imdb SELECT ?, value FROM json_each(?)").bind(user.id, JSON.stringify(ids)),
    ]);
    return reply(await everything(db, user), 200, origin);
  }

  if (path === "/me/alerts" && request.method === "PUT") {
    const current = (await alertSettings(db, user.id)) || { ...DEFAULT_ALERTS, since: null, token: null };
    const next = {};
    for (const key of ["enabled", "added", "last", "imdb", "digest", "newsletter"]) {
      const value = body?.[key] === undefined ? Boolean(current[key]) : body[key];
      if (typeof value !== "boolean") return reply({ error: "bad request" }, 400, origin);
      next[key] = value;
    }
    // Turning alerts on starts the clock: only screenings found from now on are news.
    const since = next.enabled && !current.enabled ? stamp : current.since;
    const token = current.token || toBase64url(crypto.getRandomValues(new Uint8Array(24)));
    await db
      .prepare(
        `INSERT INTO alert_settings (user_id, enabled, added, last, imdb, digest, newsletter, since, token)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT (user_id) DO UPDATE SET enabled = excluded.enabled, added = excluded.added,
           last = excluded.last, imdb = excluded.imdb, digest = excluded.digest,
           newsletter = excluded.newsletter, since = excluded.since, token = excluded.token`,
      )
      .bind(user.id, +next.enabled, +next.added, +next.last, +next.imdb, +next.digest, +next.newsletter, since, token)
      .run();
    return reply(await everything(db, user), 200, origin);
  }

  if (path === "/me/alerts/test" && request.method === "POST") {
    const settings = await alertSettings(db, user.id);
    if (!settings?.token) return reply({ error: "turn on alerts first" }, 400, origin);
    if (!env.RESEND_API_KEY) return reply({ error: "email isn't set up yet" }, 503, origin);
    try {
      await sendTest(env, user, settings, { now, fetcher });
    } catch (error) {
      return reply({ error: String(error.message || error) }, 400, origin);
    }
    return reply({ ok: true, to: user.email }, 200, origin);
  }

  return reply({ error: "not found" }, 404, origin);
}

export const isAccountPath = (pathname) => /^\/(auth|me|alerts)(\/|$)/.test(pathname);
