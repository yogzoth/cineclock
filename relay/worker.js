// cineclock relay: reads a public Letterboxd profile for the site.
//
// Letterboxd doesn't let other websites read profiles from a visitor's browser,
// and cineclock is plain files with no server, so this small Cloudflare Worker
// does the reading. GET /profile/<username> returns:
//
//   { user, watchlist: [slug], watched: { slug: rating or null },
//     diary: { slug: "YYYY-MM-DD" }, diary_since: "YYYY-MM-DD" or null }
//
// It reads the same pages as the site's own daily sync (profiles.py): every
// page of the watchlist, the first page of watched films (Letterboxd blocks
// the rest), and the diary feed. Answers are cached for six hours.

import { handleAccount, isAccountPath } from "./account.js";
import { handleImdb, isImdbPath, REPOSITORY } from "./imdb.js";
import { runAlerts } from "./alerts.js";

const BASE = "https://letterboxd.com";
const ALLOWED_ORIGINS = [
  "https://cineclock.ca",
  "https://www.cineclock.ca",
  "http://localhost:8000",
  "http://127.0.0.1:8000",
];
const CACHE_SECONDS = 6 * 60 * 60;
const FRESH_AFTER = 2 * 60 * 1000;
// Cloudflare's free plan allows 50 fetches per request; leave room for the rest.
const MAX_WATCHLIST_PAGES = 45;
// The headers a browser sends; Letterboxd's bot protection is stricter without them.
const HEADERS = {
  "User-Agent":
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36 cineclock-relay",
  Accept: "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
  "Accept-Language": "en-CA,en;q=0.9",
};

export function parseGrid(page) {
  // Each film in a poster grid is an <li class="griditem ..."> with its slug in
  // data-item-slug and, on the watched list, a rated-N class for the stars.
  const films = {};
  for (const item of page.split(/<li class="griditem/).slice(1)) {
    const slug = item.match(/data-item-slug="([^"]+)"/);
    if (!slug) continue;
    const rated = item.match(/\brated-(\d+)\b/);
    films[slug[1]] = rated ? Number(rated[1]) : null;
  }
  const pages = [...page.matchAll(/\/page\/(\d+)\//g)].map((m) => Number(m[1]));
  return { films, lastPage: Math.max(1, ...pages) };
}

export function parseRss(feed) {
  // Diary entries: the film's slug from its link, and the date it was watched.
  const diary = {};
  for (const item of feed.split("<item>").slice(1)) {
    const watched = item.match(/<letterboxd:watchedDate>(\d{4}-\d{2}-\d{2})</);
    const slug = item.match(/<link>[^<]*\/film\/([^/<]+)\//);
    if (!watched || !slug) continue;
    if (!diary[slug[1]] || watched[1] > diary[slug[1]]) diary[slug[1]] = watched[1];
  }
  return diary;
}

// Cloudflare lets a worker hold six connections open at once, so pages are
// fetched a few at a time and each one is read in full before the next batch.
const BATCH = 4;

async function read(fetcher, url) {
  const response = await fetcher(url, { headers: HEADERS });
  return { status: response.status, ok: response.ok, text: await response.text() };
}

export async function readProfile(user, fetcher = fetch) {
  const first = await read(fetcher, `${BASE}/${user}/watchlist/`);
  if (first.status === 404) return null;
  if (!first.ok) throw new Error(`Letterboxd answered ${first.status}`);
  const page1 = parseGrid(first.text);
  const watchlist = new Set(Object.keys(page1.films));

  const last = Math.min(page1.lastPage, MAX_WATCHLIST_PAGES);
  for (let start = 2; start <= last; start += BATCH) {
    const batch = [];
    for (let n = start; n < start + BATCH && n <= last; n++) {
      batch.push(read(fetcher, `${BASE}/${user}/watchlist/page/${n}/`));
    }
    for (const page of await Promise.all(batch)) {
      if (!page.ok) continue; // keep what we have rather than lose the lot
      for (const slug of Object.keys(parseGrid(page.text).films)) watchlist.add(slug);
    }
  }

  const watchedPage = await read(fetcher, `${BASE}/${user}/films/`);
  const watched = watchedPage.ok ? parseGrid(watchedPage.text).films : {};
  const rss = await read(fetcher, `${BASE}/${user}/rss/`);
  const diary = rss.ok ? parseRss(rss.text) : {};

  // Films in the diary are watched even if the first page of the watched list
  // didn't reach them.
  for (const slug of Object.keys(diary)) if (!(slug in watched)) watched[slug] = null;
  const dates = Object.values(diary).sort();
  return { user, watchlist: [...watchlist], watched, diary, diary_since: dates[0] || null };
}

function respond(body, status, origin) {
  const headers = {
    "Content-Type": "application/json",
    // Only a good answer is worth keeping; an error should be retried.
    "Cache-Control": status === 200 ? `public, max-age=${CACHE_SECONDS}` : "no-store",
    Vary: "Origin",
  };
  if (ALLOWED_ORIGINS.includes(origin)) headers["Access-Control-Allow-Origin"] = origin;
  return new Response(JSON.stringify(body), { status, headers });
}

// GitHub's own schedule runs late or not at all when it's busy, so Cloudflare's
// clock starts the site rebuild too (wrangler.toml's build crons).
export const BUILD_CRONS = ["40 */4 * * *", "20 7 * * *"];

export async function startBuild(env, fetcher = fetch) {
  if (!env.GITHUB_TOKEN) return false;
  const response = await fetcher(`https://api.github.com/repos/${REPOSITORY}/actions/workflows/deploy.yml/dispatches`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "User-Agent": "cineclock-relay",
    },
    body: JSON.stringify({ ref: "main" }),
  });
  if (!response.ok) console.log("build start:", response.status, await response.text());
  return response.ok;
}

export default {
  async scheduled(event, env, ctx) {
    if (BUILD_CRONS.includes(event.cron)) {
      ctx.waitUntil(startBuild(env));
      return;
    }
    // Email alerts, a few times each morning.
    if (!env.DB) return;
    ctx.waitUntil(runAlerts(env).then((done) => console.log("alerts:", JSON.stringify(done))));
  },

  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    if (isImdbPath(url.pathname)) {
      if (!env?.DB) return respond({ error: "accounts aren't set up" }, 503, "");
      return handleImdb(request, env).catch((error) => {
        console.error("imdb error:", error);
        return respond({ error: "server error" }, 500, request.headers.get("Origin") || "");
      });
    }
    if (isAccountPath(url.pathname)) {
      if (!env?.DB) return respond({ error: "accounts aren't set up" }, 503, "");
      return handleAccount(request, env);
    }
    const origin = request.headers.get("Origin") || "";
    const match = url.pathname.match(/^\/profile\/([A-Za-z0-9_]{1,30})\/?$/);
    if (request.method !== "GET" || !match) return respond({ error: "not found" }, 404, origin);
    const user = match[1].toLowerCase();

    const cache = typeof caches !== "undefined" ? caches.default : null;
    const key = new Request(`${url.origin}/profile/${user}`);
    // ?fresh skips the cache (the Sync button on the account page), but not for a
    // copy read in the last two minutes: each read is up to 47 Letterboxd pages,
    // and anyone can call this.
    const hit = cache && (await cache.match(key));
    if (hit) {
      const body = await hit.json();
      const age = Date.now() - Date.parse(body.read_at || 0);
      if (!url.searchParams.has("fresh") || age < FRESH_AFTER) return respond(body, 200, origin);
    }

    try {
      const profile = await readProfile(user);
      if (profile) profile.read_at = new Date().toISOString();
      if (!profile) return respond({ error: "no such profile" }, 404, origin);
      const response = respond(profile, 200, origin);
      if (cache && ctx) ctx.waitUntil(cache.put(key, response.clone()));
      return response;
    } catch (error) {
      // Say what went wrong, so a block is easy to tell from an outage.
      return respond({ error: "Letterboxd didn't answer", detail: String(error.message || error) }, 502, origin);
    }
  },
};
