# cineclock

[![CI](https://github.com/yogzoth/cineclock/actions/workflows/ci.yml/badge.svg)](https://github.com/yogzoth/cineclock/actions/workflows/ci.yml)
[![Deploy](https://github.com/yogzoth/cineclock/actions/workflows/deploy.yml/badge.svg)](https://github.com/yogzoth/cineclock/actions/workflows/deploy.yml)

Vancouver showtimes for film lovers. Indie and rep cinemas in one place.

**Live site: https://cineclock.ca/**

Version 2.2.0. See what changed in the [changelog](CHANGELOG.md).

<p>
  <img src="docs/home.png" alt="The cineclock home page on a phone: a spotlight of tonight's pick, shortcuts, and a row of posters" width="250">
  <img src="docs/films.png" alt="The Films page: a grid of posters, each with the title, year, score and dates" width="250">
  <img src="docs/film.png" alt="A film page: backdrop, poster, scores from four sources and showtimes" width="250">
</p>

## What it does

Five theatres so far: the Rio, the Park, the Cinematheque, VIFF Centre (both screens) and the Dunbar.

- **Home:** a spotlight of tonight's picks, three shortcut tiles (what's on tonight, the festival of the moment, your next saved showing), then rows: on soon, leaving soon, special screenings, just added, highly rated
- **Films:** a poster grid with each film once and the dates it's on, sortable and filterable by theatre, format, event and genre
- **Showtimes:** every screening, day by day, with the same filters, a date picker, and day chips that stay on screen and light up the day you're looking at
- **Festivals:** a page for each festival (or packed series) on, with the theatre's banner and blurb, and a page listing them all
- **Theatres:** each theatre's story, prices, room specs and photos, poster rows of what's on, then its showtimes
- **Film pages:** backdrop, poster, synopsis, scores from Letterboxd, IMDb, Rotten Tomatoes and Metacritic, and every showtime
- **One card everywhere:** every poster card has the same lines in the same spots. Anything special, like 35mm or a Q&A, is a badge on the poster's corner
- **Data:** the Rio, the Park and VIFF publish JSON feeds, which cineclock reads directly; the Cinematheque and the Dunbar have none, so their own listing pages are read. Theatre pages that link a film's IMDb entry give an exact match for posters and ratings
- **Tags** from what each theatre's listing says (70mm, 35mm, Q&A, Music, Festival, Shorts and more), plus a cheap AI read of each new listing for the ones worded unusually
- **Accounts (optional):** sign in with Google to save films, connect a Letterboxd or IMDb watchlist, pick a rating source and a profile picture, and choose emails. Everything else works without one

## Running it

Needs Python 3.12 or newer, and Node 22 for the relay tests.

```sh
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check .
pytest
for t in relay/test.mjs relay/account.test.mjs relay/alerts.test.mjs relay/imdb.test.mjs tests/when.test.mjs; do node "$t"; done
fastapi dev src/cineclock/app.py   # preview at http://127.0.0.1:8000
```

The preview serves exactly the pages the static build publishes. To scrape every venue into `cineclock.db`:

```sh
python -m cineclock.scrape        # TMDB_API_KEY and OMDB_API_KEY add film details and ratings
python -m cineclock.ai            # GEMINI_API_KEY: tag new listings (skipped without it)
python -m cineclock.festivals     # festival banners and blurbs
python -m cineclock.build         # write the site to site/
```

## How it fits together

```
theatre sites ──scrapers──▶ SQLite (Actions cache) ──build──▶ static site on GitHub Pages
                                │                                │  films.json, alerts.json
                     TMDB, OMDb, Letterboxd,                     ▼
                     Gemini, festival pages         browser ◀──▶ relay Worker (Cloudflare)
                                                                 │  D1: accounts, saves,
                                                                 │  settings, IMDb lists
                                                    GitHub job ──┘  (reads IMDb lists)
```

- **Where things live.** Screenings, film details, prices, AI notes and festival pages: SQLite, kept between runs in the Actions cache. Accounts, sessions (stored as hashes), saved films, settings, IMDb lists and email history: the Worker's D1 database. Your Letterboxd username and a copy of your account: your browser's storage.
- **Updates.** The Deploy workflow runs on every push to `main` and every 4 hours: tests, scrape, film details, AI tags, festival pages, build, publish, then a health check that fails (and emails) if a theatre hasn't updated in about 12 hours. A theatre that fails or returns nothing keeps its last listings; one that suddenly loses most of its shows is held, not wiped. Busy or slow sites are asked again twice before giving up.
- **Times** are stored in UTC and shown in Vancouver time from the pinned `tzdata` package. The browser and the email job use the build's fixed offset (UTC-7), so B.C.'s move to permanent daylight time can't shift Today or Tonight on devices with older time zone data.
- **The relay** (`relay/`, deployed by Cloudflare on every push that changes it) checks Google's signature on sign-in, keeps sessions in an HttpOnly cookie, only accepts changes from the site's own address, reads Letterboxd profiles, and runs the email job.

### Secrets

| Where | Name | What for |
| --- | --- | --- |
| GitHub | `TMDB_API_KEY`, `OMDB_API_KEY` | Film details and ratings |
| GitHub | `GEMINI_API_KEY` | AI tags and the Monday report (optional) |
| Cloudflare Worker | `GITHUB_TOKEN` | Start the IMDb sync job straight away (optional) |
| Cloudflare Worker | `RESEND_API_KEY` | Send emails. Not set yet, so no emails go out |

### If a release goes wrong

- **Site:** revert the commit on `main` (`git revert <sha>`, push). Deploy rebuilds from the same database, so no data is lost. To redeploy without a code change, run the Deploy workflow by hand.
- **Relay:** the revert redeploys it too. In Cloudflare, Workers & Pages, cineclock-relay, Deployments, an earlier version can also be rolled back to in one tap. D1 changes only ever add tables or columns, so older code still runs against it.
- **Emails:** remove the `RESEND_API_KEY` secret (or the cron in `relay/wrangler.toml`) to stop sending at once; settings and history stay.
- **Database:** don't delete the Actions cache; it holds screening history. A bad scrape is undone by the next good one.

## Credits

Inspired by [MovieClock](https://movieclock.app) by Jordan Hudson ([source](https://github.com/jordanhudson/moviecal), MIT licensed). Some scraper approaches are borrowed from it.

## Fonts

Instrument Sans and Source Serif 4, both under the SIL Open Font License (see `src/cineclock/static/fonts`).

## License

MIT. See [LICENSE](LICENSE).
