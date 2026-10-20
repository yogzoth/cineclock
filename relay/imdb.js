// IMDb watchlists by username (an id like ur12345678; IMDb has no other kind).
//
// IMDb's API turns away Cloudflare's servers but answers GitHub's, so the
// reading happens in a GitHub Actions job (.github/workflows/imdb-sync.yml)
// and the lists are kept here:
//
//   POST /imdb/connect  { user }   ask for a watchlist; returns what's known so far
//   POST /imdb/sync     { user }   read it again now (the Sync button, or a stale visit)
//   GET  /imdb/<user>              the watchlist as last read
//   GET  /imdb/wanted              lists due a read      (GitHub job only)
//   PUT  /imdb/lists    { lists }  the job's results     (GitHub job only)
//
// Lists are read when asked for: on connect, on Sync, and when someone visits
// with a copy more than half a day old. The Worker starts the job straight away
// (needs the GITHUB_TOKEN secret: a token that can run this repo's workflows),
// at most once a minute. A daily run catches lists nobody has looked at, so
// email alerts stay current.
//
// The job proves who it is with a GitHub OIDC token: a JWT GitHub signs for
// one run of one repository, so no shared secret is needed.

import { decodeJson, fromBase64url, preflight, reply, sessionUser, SITE_ORIGINS } from "./account.js";

const GITHUB_ISSUER = "https://token.actions.githubusercontent.com";
const GITHUB_KEYS_URL = `${GITHUB_ISSUER}/.well-known/jwks`;
export const AUDIENCE = "cineclock-relay";
export const REPOSITORY = "yogzoth/cineclock";

const USER = /^(ur\d{4,12}|p\.[a-z0-9]{8,40})$/;
const TITLE = /^tt\d{5,10}$/;
const HOUR = 3600 * 1000;
const REFRESH = 24 * HOUR;
const RESYNC = 2 * 60 * 1000;
const FORGET = 60 * 24 * HOUR;
const MAX_LISTS = 2000;
const MAX_TITLES = 10000;

const SCHEMA = [
  `CREATE TABLE IF NOT EXISTS imdb_lists (
    user TEXT PRIMARY KEY,
    ids TEXT,
    error TEXT,
    updated INTEGER,
    requested INTEGER NOT NULL
  )`,
  "CREATE TABLE IF NOT EXISTS imdb_runs (id INTEGER PRIMARY KEY CHECK (id = 1), started INTEGER NOT NULL)",
];

const WORKFLOW = `https://api.github.com/repos/${REPOSITORY}/actions/workflows/imdb-sync.yml/dispatches`;

// Start the sync job now, unless it was started in the last minute.
export async function startSync(env, now, fetcher = fetch) {
  if (!env.GITHUB_TOKEN) return false;
  const claimed = await env.DB.prepare(
    `INSERT INTO imdb_runs (id, started) VALUES (1, ?)
     ON CONFLICT(id) DO UPDATE SET started = excluded.started WHERE started < ?`,
  )
    .bind(now, now - 60 * 1000)
    .run();
  if (!claimed.meta?.changes && !claimed.changes) return false;
  const response = await fetcher(WORKFLOW, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "cineclock-relay",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!response.ok) console.log("imdb sync start:", response.status, await response.text());
  return response.ok;
}

// "ur12345678", a profile id like "p.h2ouytvlcs...", or any IMDb link with one in it.
export function imdbUser(text) {
  const match = String(text || "").match(/\b(ur\d{4,12}|p\.[a-z0-9]{8,40})\b/i);
  return match ? match[0].toLowerCase() : null;
}

let keyCache = { keys: null, until: 0 };

async function githubKeys(fetcher, now) {
  if (keyCache.keys && now < keyCache.until) return keyCache.keys;
  const response = await fetcher(GITHUB_KEYS_URL);
  if (!response.ok) throw new Error(`GitHub keys: ${response.status}`);
  const { keys } = await response.json();
  keyCache = { keys, until: now + HOUR };
  return keys;
}

// True when the request carries a token GitHub signed for a run in this repo.
export async function fromOurJob(request, keys, now = Date.now()) {
  const token = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
  const parts = token.split(".");
  if (parts.length !== 3) return false;
  let header, claims;
  try {
    header = decodeJson(parts[0]);
    claims = decodeJson(parts[1]);
  } catch {
    return false;
  }
  const jwk = keys.find((k) => k.kid === header.kid);
  if (header.alg !== "RS256" || !jwk) return false;
  const key = await crypto.subtle.importKey("jwk", jwk, { name: "RSASSA-PKCS1-v1_5", hash: "SHA-256" }, false, [
    "verify",
  ]);
  const signed = new TextEncoder().encode(`${parts[0]}.${parts[1]}`);
  if (!(await crypto.subtle.verify("RSASSA-PKCS1-v1_5", key, fromBase64url(parts[2]), signed))) return false;
  return (
    claims.iss === GITHUB_ISSUER &&
    claims.aud === AUDIENCE &&
    claims.repository === REPOSITORY &&
    claims.exp * 1000 > now
  );
}

function shown(row) {
  return {
    user: row.user,
    ids: row.ids ? JSON.parse(row.ids) : [],
    ready: Boolean(row.updated),
    updated: row.updated ? new Date(row.updated).toISOString() : null,
    error: row.error || null,
    name: row.name || null,
  };
}

const ready = new WeakSet();

export async function handleImdb(request, env, { now = Date.now(), fetcher = fetch, keys = null } = {}) {
  const origin = request.headers.get("Origin") || "";
  if (request.method === "OPTIONS") return preflight(origin);
  const db = env.DB;
  if (!ready.has(db)) {
    await db.batch(SCHEMA.map((sql) => db.prepare(sql)));
    // Added after the table first shipped.
    for (const column of ["name TEXT", "asked INTEGER"]) {
      await db.prepare(`ALTER TABLE imdb_lists ADD COLUMN ${column}`).run().catch(() => {});
    }
    ready.add(db);
  }
  const path = new URL(request.url).pathname.replace(/\/+$/, "");

  // Connecting or syncing queues work for the GitHub job and takes one of the
  // MAX_LISTS places, so it needs a signed-in account.
  const asks = ["/imdb/connect", "/imdb/sync"].includes(path) && request.method === "POST";
  if (asks && !SITE_ORIGINS.includes(origin)) return reply({ error: "forbidden" }, 403, origin);
  if (asks && !(await sessionUser(db, request, now))) return reply({ error: "signed out" }, 401, origin);

  if (path === "/imdb/connect" && request.method === "POST") {
    const body = await request.json().catch(() => ({}));
    const user = imdbUser(body?.user);
    if (!user) return reply({ error: "no IMDb user id" }, 400, origin);
    const known = await db.prepare("SELECT * FROM imdb_lists WHERE user = ?").bind(user).first();
    if (!known) {
      const { count } = await db.prepare("SELECT COUNT(*) AS count FROM imdb_lists").first();
      if (count >= MAX_LISTS) return reply({ error: "full" }, 503, origin);
    }
    await db
      .prepare(
        `INSERT INTO imdb_lists (user, requested, asked) VALUES (?, ?, ?) ON CONFLICT(user) DO UPDATE SET
           requested = excluded.requested, asked = excluded.asked,
           updated = CASE WHEN error IS NULL THEN updated END`,
      )
      .bind(user, now, now)
      .run();
    const row = await db.prepare("SELECT * FROM imdb_lists WHERE user = ?").bind(user).first();
    if (!row.updated) await startSync(env, now, fetcher).catch((error) => console.log("imdb sync start:", error));
    return reply(shown(row), 200, origin);
  }

  if (path === "/imdb/sync" && request.method === "POST") {
    const body = await request.json().catch(() => ({}));
    const user = imdbUser(body?.user);
    const row = user && (await db.prepare("SELECT * FROM imdb_lists WHERE user = ?").bind(user).first());
    if (!row) return reply({ error: "not connected" }, 404, origin);
    if (!(row.updated && now - row.updated < RESYNC)) {
      await db.prepare("UPDATE imdb_lists SET asked = ?, requested = ? WHERE user = ?").bind(now, now, user).run();
      await startSync(env, now, fetcher).catch((error) => console.log("imdb sync start:", error));
    }
    return reply(shown(row), 200, origin);
  }

  if (path === "/imdb/wanted" || path === "/imdb/lists") {
    if (!(await fromOurJob(request, keys || (await githubKeys(fetcher, now)), now))) {
      return reply({ error: "forbidden" }, 403, origin);
    }
    if (request.method === "GET" && path === "/imdb/wanted") {
      const rows = await db
        .prepare(
          `SELECT user FROM imdb_lists WHERE requested > ?
             AND (updated IS NULL OR updated < ? OR asked > updated OR (name IS NULL AND user LIKE 'p.%'))
           ORDER BY updated IS NOT NULL, updated LIMIT 100`,
        )
        .bind(now - FORGET, now - REFRESH)
        .all();
      return reply({ users: rows.results.map((r) => r.user) }, 200, origin);
    }
    if (request.method === "PUT" && path === "/imdb/lists") {
      const body = await request.json().catch(() => ({}));
      const lists = Array.isArray(body?.lists) ? body.lists : [];
      const updates = [];
      for (const list of lists) {
        if (!USER.test(list?.user)) continue;
        const ids = Array.isArray(list.ids) ? list.ids.filter((id) => TITLE.test(id)).slice(0, MAX_TITLES) : null;
        updates.push(
          db
            .prepare("UPDATE imdb_lists SET ids = COALESCE(?, ids), error = ?, updated = ?, name = COALESCE(?, name) WHERE user = ?")
            .bind(
              ids ? JSON.stringify(ids) : null,
              ids ? null : String(list.error || "unreadable").slice(0, 40),
              now,
              typeof list.name === "string" ? list.name.slice(0, 60) : null,
              list.user,
            ),
        );
      }
      if (updates.length) await db.batch(updates);
      return reply({ saved: updates.length }, 200, origin);
    }
  }

  const one = path.match(/^\/imdb\/(ur\d{4,12}|p\.[a-z0-9]{8,40})$/);
  if (one && request.method === "GET") {
    const row = await db.prepare("SELECT * FROM imdb_lists WHERE user = ?").bind(one[1]).first();
    if (!row) return reply({ error: "not connected" }, 404, origin);
    return reply(shown(row), 200, origin);
  }

  return reply({ error: "not found" }, 404, origin);
}

export const isImdbPath = (pathname) => /^\/imdb(\/|$)/.test(pathname);
