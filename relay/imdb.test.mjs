// Tests for IMDb watchlists by username, run with: node relay/imdb.test.mjs
import assert from "node:assert/strict";
import { DatabaseSync } from "node:sqlite";
import { AUDIENCE, handleImdb, imdbUser, REPOSITORY } from "./imdb.js";
import { setup } from "./account.js";

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
const HOUR = 3600 * 1000;
const pair = await crypto.subtle.generateKey(
  { name: "RSASSA-PKCS1-v1_5", modulusLength: 2048, publicExponent: new Uint8Array([1, 0, 1]), hash: "SHA-256" },
  true,
  ["sign", "verify"],
);
const keys = [{ ...(await crypto.subtle.exportKey("jwk", pair.publicKey)), kid: "gh", alg: "RS256" }];
const b64 = (data) => Buffer.from(JSON.stringify(data)).toString("base64url");

async function githubToken(claims = {}) {
  const head = b64({ alg: "RS256", kid: "gh", typ: "JWT" });
  const body = b64({
    iss: "https://token.actions.githubusercontent.com",
    aud: AUDIENCE,
    repository: REPOSITORY,
    exp: NOW / 1000 + 600,
    ...claims,
  });
  const signature = await crypto.subtle.sign("RSASSA-PKCS1-v1_5", pair.privateKey, new TextEncoder().encode(`${head}.${body}`));
  return `${head}.${body}.${Buffer.from(signature).toString("base64url")}`;
}

const started = [];
const fakeGithub = async (url, init) => {
  started.push({ url, auth: init.headers.Authorization, body: init.body });
  return new Response(null, { status: 204 });
};

// A signed-in visitor: a user and session row, sent as the session cookie.
async function signedIn(env) {
  await setup(env.DB);
  env.DB.sqlite.prepare("INSERT INTO users (google_sub, email, created) VALUES ('1', 'jo@example.com', 'x')").run();
  const id = "test-session-id-0123456789";
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(id));
  const hash = Buffer.from(digest).toString("base64url");
  env.DB.sqlite.prepare("INSERT INTO sessions VALUES (?, 1, '2099-01-01')").run(hash);
  env.session = id;
  return env;
}

async function call(env, method, path, { body, token, origin = "https://cineclock.ca", now = NOW, anonymous = false } = {}) {
  const headers = { Origin: origin };
  if (env.session && !anonymous) headers.Cookie = `cc_session=${env.session}`;
  if (token) headers.Authorization = `Bearer ${token}`;
  const request = new Request(`https://api.cineclock.ca${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const response = await handleImdb(request, env, { now, keys, fetcher: fakeGithub });
  return { status: response.status, json: await response.json() };
}

// Links and bare ids both work.
assert.equal(imdbUser("https://www.imdb.com/user/ur12345678/watchlist/?ref_=nv"), "ur12345678");
assert.equal(imdbUser("m.imdb.com/user/ur999999/"), "ur999999");
assert.equal(imdbUser("yogzoth"), null);
assert.equal(imdbUser("https://www.imdb.com/user/p.h2ouytvlcsyuesmhpcj673m3gq?ref_=wl_usr_ov"), "p.h2ouytvlcsyuesmhpcj673m3gq");

const env = await signedIn({ DB: fakeD1() });

// Connecting or syncing needs an account.
assert.equal((await call(env, "POST", "/imdb/connect", { body: { user: "ur12345678" }, anonymous: true })).status, 401);
assert.equal((await call(env, "POST", "/imdb/sync", { body: { user: "ur12345678" }, anonymous: true })).status, 401);

// Connecting starts a pending list.
let r = await call(env, "POST", "/imdb/connect", { body: { user: "https://www.imdb.com/user/ur12345678/" } });
assert.equal(r.status, 200);
assert.deepEqual(r.json, { user: "ur12345678", ids: [], ready: false, updated: null, error: null, name: null });
assert.equal((await call(env, "POST", "/imdb/connect", { body: { user: "nope" } })).status, 400);
assert.equal((await call(env, "POST", "/imdb/connect", { body: { user: "ur1234" }, origin: "https://evil.example" })).status, 403);

// Only the GitHub job can see what's wanted or post results.
assert.equal((await call(env, "GET", "/imdb/wanted")).status, 403);
assert.equal((await call(env, "GET", "/imdb/wanted", { token: await githubToken({ repository: "someone/else" }) })).status, 403);
assert.equal((await call(env, "GET", "/imdb/wanted", { token: await githubToken({ aud: "other" }) })).status, 403);
assert.equal((await call(env, "GET", "/imdb/wanted", { token: await githubToken({ exp: NOW / 1000 - 1 }) })).status, 403);
const token = await githubToken();
r = await call(env, "GET", "/imdb/wanted", { token });
assert.deepEqual(r.json.users, ["ur12345678"]);

// The job's results show up for the browser, junk ids dropped.
r = await call(env, "PUT", "/imdb/lists", {
  token,
  body: { lists: [{ user: "ur12345678", ids: ["tt0073195", "nope"], name: "JordanM-078" }] },
});
assert.equal(r.json.saved, 1);
r = await call(env, "GET", "/imdb/ur12345678");
assert.deepEqual(r.json.ids, ["tt0073195"]);
assert.equal(r.json.ready, true);
assert.equal(r.json.name, "JordanM-078");

// Fresh lists aren't wanted again until they're a day old, or someone asks.
assert.deepEqual((await call(env, "GET", "/imdb/wanted", { token })).json.users, []);
assert.deepEqual((await call(env, "GET", "/imdb/wanted", { token: await githubToken({ exp: NOW / 1000 + 26 * 3600 }), now: NOW + 25 * HOUR })).json.users, ["ur12345678"]);

// Sync asks for a read now, but not again within two minutes of the last one.
const synced = started.length;
const later = await githubToken({ exp: NOW / 1000 + 3 * 3600 });
r = await call(env, "POST", "/imdb/sync", { body: { user: "ur12345678" }, now: NOW + 60 * 1000 });
assert.deepEqual((await call(env, "GET", "/imdb/wanted", { token })).json.users, []);
r = await call(env, "POST", "/imdb/sync", { body: { user: "ur12345678" }, now: NOW + HOUR });
assert.deepEqual((await call(env, "GET", "/imdb/wanted", { token: later, now: NOW + HOUR })).json.users, ["ur12345678"]);
assert.equal(started.length, synced);  // no GitHub token on this env
assert.equal((await call(env, "POST", "/imdb/sync", { body: { user: "ur9999999" } })).status, 404);
await call(env, "PUT", "/imdb/lists", { token: later, body: { lists: [{ user: "ur12345678", ids: ["tt0073195"] }] }, now: NOW + HOUR });

// A list that went private keeps its last ids and says why.
await call(env, "PUT", "/imdb/lists", { token, body: { lists: [{ user: "ur12345678", error: "private" }] } });
r = await call(env, "GET", "/imdb/ur12345678");
assert.deepEqual(r.json.ids, ["tt0073195"]);
assert.equal(r.json.error, "private");

// Connecting again after fixing it gets it read on the next run.
await call(env, "POST", "/imdb/connect", { body: { user: "ur12345678" } });
assert.deepEqual((await call(env, "GET", "/imdb/wanted", { token })).json.users, ["ur12345678"]);

assert.equal((await call(env, "GET", "/imdb/ur5555555")).status, 404);

// Without a GitHub token, connecting just waits for the next scheduled run.
assert.equal(started.length, 0);

// With one, a new list starts the job straight away, at most once a minute.
const live = await signedIn({ DB: fakeD1(), GITHUB_TOKEN: "ghp_test" });
await call(live, "POST", "/imdb/connect", { body: { user: "ur1111111" } });
await call(live, "POST", "/imdb/connect", { body: { user: "ur2222222" }, now: NOW + 30 * 1000 });
assert.equal(started.length, 1);
assert.match(started[0].url, /yogzoth\/cineclock\/actions\/workflows\/imdb-sync.yml\/dispatches$/);
assert.equal(started[0].auth, "Bearer ghp_test");
assert.deepEqual(JSON.parse(started[0].body), { ref: "main" });
await call(live, "POST", "/imdb/connect", { body: { user: "ur2222222" }, now: NOW + 61 * 1000 });
assert.equal(started.length, 2);

// A list that's already been read doesn't start it.
const liveToken = await githubToken({ exp: NOW / 1000 + 3600 });
await call(live, "PUT", "/imdb/lists", { token: liveToken, body: { lists: [{ user: "ur1111111", ids: [] }] }, now: NOW + 2 * 60 * 1000 });
await call(live, "POST", "/imdb/connect", { body: { user: "ur1111111" }, now: NOW + 5 * 60 * 1000 });
assert.equal(started.length, 2);
console.log("imdb tests passed");
