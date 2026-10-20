// Tests for accounts, run with: node relay/account.test.mjs
// A stand-in for D1 on Node's built-in SQLite, and a stand-in Google that signs
// tokens with a key made here.
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { GOOGLE_CLIENT_ID, handleAccount, verifyGoogleToken } from "./account.js";
import worker from "./worker.js";

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
      sqlite.exec("BEGIN");
      try {
        for (const s of statements) sqlite.prepare(s.sql).run(...s.args);
        sqlite.exec("COMMIT");
      } catch (error) {
        sqlite.exec("ROLLBACK");
        throw error;
      }
    },
    sqlite,
  };
}

const NOW = Date.parse("2026-10-19T12:00:00Z");
const keyPair = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true,
  ["sign", "verify"],
);
const jwk = { ...(await crypto.subtle.exportKey("jwk", keyPair.publicKey)), kid: "test-key", alg: "RS256" };
const b64 = (data) =>
  Buffer.from(typeof data === "string" ? data : JSON.stringify(data)).toString("base64url");

async function googleToken(claims, key = keyPair.privateKey, kid = "test-key") {
  const head = b64({ alg: "RS256", kid, typ: "JWT" });
  const body = b64({
    iss: "https://accounts.google.com",
    aud: GOOGLE_CLIENT_ID,
    sub: "1001",
    email: "jo@example.com",
    email_verified: true,
    exp: NOW / 1000 + 3600,
    ...claims,
  });
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", key, new TextEncoder().encode(`${head}.${body}`));
  return `${head}.${body}.${Buffer.from(signature).toString("base64url")}`;
}

const googleFetch = async () => new Response(JSON.stringify({ keys: [jwk] }));
const SITE = "https://cineclock.ca";

function call(env, method, path, { body, cookie, origin = SITE } = {}) {
  const headers = { Origin: origin };
  if (cookie) headers.Cookie = cookie;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const request = new Request(`https://api.cineclock.ca${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  return handleAccount(request, env, { now: NOW, fetcher: googleFetch });
}

async function signIn(env, claims = {}) {
  const response = await call(env, "POST", "/auth/google", { body: { credential: await googleToken(claims) } });
  assert.equal(response.status, 200);
  const cookie = response.headers.get("Set-Cookie");
  assert.match(cookie, /HttpOnly; Secure; SameSite=Lax/);
  return cookie.split(";")[0];
}

// Token checks: Google's signature, our client id, not expired.
{
  const keys = [jwk];
  assert.equal((await verifyGoogleToken(await googleToken({}), keys, NOW)).sub, "1001");
  assert.equal(await verifyGoogleToken(await googleToken({ aud: "someone-else" }), keys, NOW), null);
  assert.equal(await verifyGoogleToken(await googleToken({ exp: NOW / 1000 - 1 }), keys, NOW), null);
  assert.equal(await verifyGoogleToken(await googleToken({ iss: "evil.example" }), keys, NOW), null);
  const stranger = await crypto.subtle.generateKey(
    { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
    true,
    ["sign"],
  );
  assert.equal(await verifyGoogleToken(await googleToken({}, stranger.privateKey), keys, NOW), null);
  assert.equal(await verifyGoogleToken("not.a.token", keys, NOW), null);
}

// Sign in, save, merge, settings, IMDb list, sign out.
{
  const env = { DB: fakeD1() };
  assert.equal((await call(env, "GET", "/me")).status, 401);
  const failed = await call(env, "POST", "/auth/google", { body: { credential: "junk" } });
  assert.equal(failed.status, 401);

  const cookie = await signIn(env);
  let me = await (await call(env, "GET", "/me", { cookie })).json();
  assert.deepEqual(me, {
    email: "jo@example.com",
    letterboxd: null,
    rating_source: "letterboxd",
    avatar: null,
    saved: { film: [], theatre: [] },
    imdb: [],
    alerts: { enabled: false, added: true, last: true, imdb: true, digest: false, newsletter: false },
    email_ready: false,
  });

  // Saving twice keeps one row; unsaving removes it.
  for (let i = 0; i < 2; i++) {
    await call(env, "PUT", "/me/saved", { cookie, body: { kind: "film", ref: "naza-2026", saved: true } });
  }
  await call(env, "PUT", "/me/saved", { cookie, body: { kind: "theatre", ref: "rio", saved: true } });
  me = await (await call(env, "GET", "/me", { cookie })).json();
  assert.deepEqual(me.saved, { film: ["naza-2026"], theatre: ["rio"] });
  await call(env, "PUT", "/me/saved", { cookie, body: { kind: "theatre", ref: "rio", saved: false } });

  // Saves made before signing in are merged, nothing dropped, junk ignored.
  const merged = await call(env, "POST", "/me/saved", {
    cookie,
    body: { items: [{ kind: "film", ref: "naza-2026" }, { kind: "film", ref: "jaws-1975" }, { kind: "film", ref: "<script>" }, { kind: "user", ref: "x" }] },
  });
  assert.deepEqual((await merged.json()).saved.film, ["naza-2026", "jaws-1975"]);

  const bad = await call(env, "PUT", "/me/saved", { cookie, body: { kind: "film", ref: "../x", saved: true } });
  assert.equal(bad.status, 400);

  const settings = await call(env, "PUT", "/me/settings", { cookie, body: { rating_source: "imdb", letterboxd: "yogzoth" } });
  assert.equal((await settings.json()).rating_source, "imdb");
  assert.equal((await call(env, "PUT", "/me/settings", { cookie, body: { rating_source: "tiktok" } })).status, 400);

  await call(env, "PUT", "/me/imdb", { cookie, body: { ids: ["tt0073195", "tt0073195", "nope", "tt1234567"] } });
  me = await (await call(env, "GET", "/me", { cookie })).json();
  assert.deepEqual(me.imdb.sort(), ["tt0073195", "tt1234567"]);
  assert.equal(me.letterboxd, "yogzoth");

  // Another site can't change anything, even with the cookie.
  const forged = await call(env, "PUT", "/me/saved", {
    cookie,
    origin: "https://evil.example",
    body: { kind: "film", ref: "x-1", saved: true },
  });
  assert.equal(forged.status, 403);

  // Signing in again reuses the account.
  const again = await signIn(env, { email: "jo@new.example" });
  assert.equal((await (await call(env, "GET", "/me", { cookie: again })).json()).saved.film.length, 2);

  const out = await call(env, "POST", "/auth/signout", { cookie });
  assert.match(out.headers.get("Set-Cookie"), /Max-Age=0/);
  assert.equal((await call(env, "GET", "/me", { cookie })).status, 401);
  assert.equal((await call(env, "GET", "/me", { cookie: again })).status, 200);
}

// One user can't see another's saves, and deleting an account removes everything.
{
  const env = { DB: fakeD1() };
  const jo = await signIn(env, { sub: "1", email: "jo@example.com" });
  const sam = await signIn(env, { sub: "2", email: "sam@example.com" });
  await call(env, "PUT", "/me/saved", { cookie: jo, body: { kind: "film", ref: "jaws-1975", saved: true } });
  await call(env, "PUT", "/me/imdb", { cookie: jo, body: { ids: ["tt0073195"] } });
  assert.deepEqual((await (await call(env, "GET", "/me", { cookie: sam })).json()).saved.film, []);

  const deleted = await call(env, "DELETE", "/me", { cookie: jo });
  assert.equal(deleted.status, 200);
  assert.equal((await call(env, "GET", "/me", { cookie: jo })).status, 401);
  for (const table of ["users", "sessions", "saved", "imdb"]) {
    const rows = env.DB.sqlite.prepare(`SELECT COUNT(*) AS n FROM ${table} WHERE ${table === "users" ? "id" : "user_id"} = 1`).get();
    assert.equal(rows.n, 0, table);
  }
  assert.equal((await call(env, "GET", "/me", { cookie: sam })).status, 200);
}

// Session ids are stored hashed, not as the cookie value.
{
  const env = { DB: fakeD1() };
  const cookie = await signIn(env);
  const id = cookie.split("=")[1];
  assert.equal(env.DB.sqlite.prepare("SELECT COUNT(*) AS n FROM sessions WHERE hash = ?").get(id).n, 0);
}

// The site may ask; credentials are allowed only for the site's own origins.
{
  const env = { DB: fakeD1() };
  const pre = await call(env, "OPTIONS", "/me/saved");
  assert.equal(pre.headers.get("Access-Control-Allow-Origin"), SITE);
  assert.equal(pre.headers.get("Access-Control-Allow-Credentials"), "true");
  const other = await call(env, "GET", "/me", { origin: "https://evil.example" });
  assert.equal(other.headers.get("Access-Control-Allow-Origin"), null);
}

// The Worker sends account paths here, and says so plainly without a database.
{
  const response = await worker.fetch(new Request("https://api.cineclock.ca/me"), {}, null);
  assert.equal(response.status, 503);
}

console.log("account tests passed");

// The same session works as a Bearer token, for the workers.dev fallback.
{
  const env = { DB: fakeD1() };
  const response = await call(env, "POST", "/auth/google", { body: { credential: await googleToken({}) } });
  const { token } = await response.json();
  assert.match(token, /^[A-Za-z0-9_-]{40,}$/);
  const request = new Request("https://cineclock-relay.cineclock-relay.workers.dev/me", {
    headers: { Origin: SITE, Authorization: `Bearer ${token}` },
  });
  const me = await handleAccount(request, env, { now: NOW, fetcher: googleFetch });
  assert.equal(me.status, 200);
  const junk = new Request("https://x/me", { headers: { Origin: SITE, Authorization: "Bearer short" } });
  assert.equal((await handleAccount(junk, env, { now: NOW, fetcher: googleFetch })).status, 401);
  const pre = await call(env, "OPTIONS", "/me");
  assert.match(pre.headers.get("Access-Control-Allow-Headers"), /Authorization/);
  console.log("bearer tests passed");
}

// A profile picture from the list, or none; anything else is refused.
{
  const env = { DB: fakeD1() };
  const cookie = await signIn(env);
  let r = await call(env, "PUT", "/me/settings", { cookie, body: { avatar: "popcorn" } });
  assert.equal((await r.json()).avatar, "popcorn");
  r = await call(env, "PUT", "/me/settings", { cookie, body: { avatar: "../../etc" } });
  assert.equal(r.status, 400);
  r = await call(env, "PUT", "/me/settings", { cookie, body: { avatar: null } });
  assert.equal((await r.json()).avatar, null);
  r = await call(env, "PUT", "/me/alerts", { cookie, body: { digest: true, newsletter: true } });
  const alerts = (await r.json()).alerts;
  assert.equal(alerts.digest, true);
  assert.equal(alerts.newsletter, true);
  console.log("avatar and email choice tests passed");
}

// Hardening: unverified emails aren't kept, crashes don't leak details, and
// oversized bodies are refused before they're read.
{
  const env = { DB: fakeD1() };
  const cookie = await signIn(env, { sub: "2002", email: "spoof@example.com", email_verified: false });
  const me = await (await call(env, "GET", "/me", { cookie })).json();
  assert.equal(me.email, null);

  const broken = { DB: { ...fakeD1(), batch: async () => { throw new Error("D1 internal: table users at /var/x"); } } };
  const crash = await call(broken, "GET", "/me");
  assert.equal(crash.status, 500);
  assert.deepEqual(await crash.json(), { error: "server error" });

  const big = new Request("https://api.cineclock.ca/me/imdb", {
    method: "PUT",
    headers: { Origin: SITE, Cookie: cookie, "Content-Type": "application/json", "Content-Length": String(600 * 1024) },
    body: "{}",
  });
  assert.equal((await handleAccount(big, env, { now: NOW, fetcher: googleFetch })).status, 413);
  console.log("hardening tests passed");
}
