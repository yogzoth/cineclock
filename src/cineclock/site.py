"""Every page of the site, built from the database.

Home (the spotlight and rows from shelves.py), Films, Showtimes, a page per
film and per theatre, the Letterboxd page, a 404 page, films.json, a sitemap
and each synced member's lists. pages() returns them all as {path: content},
which the static build writes to disk and the dev server serves from memory.
"""

import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from sqlite3 import Connection

from markupsafe import escape

from cineclock import db, personal, prices, profiles
from cineclock.films import match_key
from cineclock.listings import (
    FilmPage,
    calendar_months,
    film_pages,
    group_days,
    tags_for,
    theatre_runs,
    when,
)
from cineclock.models import VANCOUVER
from cineclock.render import STATIC, env, score_text, score_value
from cineclock.shelves import highlights, leads, rails, theatre_rails
from cineclock.tags import filter_slugs, kind, slug, sort_tags
from cineclock.timefmt import clock, stamp
from cineclock.venues import VENUES, order, room_label, shown_specs

# films.json is read from pages at different depths, so cards are drawn with this
# stand-in for the path to the site root and the browser fills in the real one.
ROOT_MARK = "@root@"
SOURCES = ("letterboxd", "imdb", "rt", "metacritic")
# Profile pictures to pick from (static/avatars/<key>.jpg), cropped by hand from
# Wikimedia Commons photos. The relay's AVATARS list has the same keys.
AVATARS = (
    ("popcorn", "Popcorn"),
    ("candy", "Candy"),
    ("carpet", "Carpet"),
    ("negative", "35mm negative"),
    ("curtain", "Curtain"),
)


def avatar_credits() -> dict:
    """Who took each avatar's photo, and its licence, from static/avatars/credits.json."""
    path = STATIC / "avatars" / "credits.json"
    found = json.loads(path.read_text()) if path.exists() else {}
    unknown = {"author": "Unknown", "licence": "", "page": "https://commons.wikimedia.org/"}
    return {key: found.get(key, unknown) for key, _ in AVATARS}


def shelf_index(film_list: list[FilmPage], now: datetime) -> list[dict]:
    """films.json: each film's finished poster card, so the Letterboxd rows drawn
    in the browser look exactly like every other card."""
    parts = env.get_template("_parts.html").module
    index = [
        {
            "slug": page.slug,
            "title": str(escape(page.title)),
            "lb": personal.letterboxd_slug(page.film.letterboxd_url) if page.film else None,
        }
        for page in film_list
    ]
    for entry, page in zip(index, film_list, strict=True):
        label = stamp(when(page.next_showing, now), page.next_showing.starts_at, "card")
        entry["card"] = str(parts.film_card(page, ROOT_MARK, label)).strip()
        if page.film and page.film.imdb_id:
            entry["imdb"] = page.film.imdb_id
        # Every score the film has, for visitors who rank by another source:
        # the number to sort by and the card's score as drawn.
        scores = {s: score_value(page.film, s) for s in SOURCES}
        scores = {s: v for s, v in scores.items() if score_text(page.film, s)}
        if scores:
            entry["scores"] = scores
            entry["rated"] = {
                s: str(parts.source_rating(page.film, s, ROOT_MARK)).strip() for s in scores
            }
        if page.film and page.film.letterboxd_count:
            entry["lb_count"] = page.film.letterboxd_count
        days = sorted({s.local_start.date().isoformat() for s in page.screenings})
        entry["days"] = days
        entry["last"] = days[-1]
    return index


def alert_feed(film_list: list[FilmPage]) -> list[dict]:
    """alerts.json: every upcoming screening of every film, with when cineclock
    first saw it, for the email alerts the Worker sends (relay/alerts.js)."""
    feed = []
    for page in film_list:
        poster = page.poster_url
        feed.append(
            {
                "slug": page.slug,
                "title": page.title,
                "year": page.year,
                "imdb": page.film.imdb_id if page.film else None,
                "poster": poster if poster and poster.startswith("http") else None,
                "screenings": [
                    {
                        "at": s.starts_at.isoformat(),
                        "venue": s.venue,
                        "room": room_label(s.venue, s.room) or None,
                        "url": s.url,
                        "status": s.status,
                        "seen": s.first_seen.isoformat() if s.first_seen else None,
                    }
                    for s in sorted(page.screenings, key=lambda s: s.starts_at)
                ],
            }
        )
    return feed


def updated_label(now: datetime) -> str:
    local = now.astimezone(VANCOUVER)
    return f"{local:%a %b} {local.day}, {clock(now)}"


def updated_day(now: datetime) -> str:
    """The date part alone, which phones drop from the home page's Updated line."""
    local = now.astimezone(VANCOUVER)
    return f"{local:%a %b} {local.day}"


def pages(conn: Connection, now: datetime) -> dict[str, str]:
    screenings = db.upcoming(conn, now)
    films = db.films_by_key(conn)
    page_of = film_pages(screenings, films)
    film_list = list({id(p): p for p in page_of.values()}.values())
    days = group_days(screenings, page_of, films, now)
    debuts = db.venue_debuts(conn)
    venue_prices = prices.current(
        {venue: prices.from_json(data) for venue, (data, _) in db.load_prices(conn).items()}
    )
    common = {
        "updated": updated_label(now),
        "updated_day": updated_day(now),
        "updated_time": clock(now),
        "venues": VENUES,
        "now": now,
    }

    def render(template: str, path: str, root: str | None = None, **context) -> str:
        root = "../" * path.count("/") if root is None else root
        return env.get_template(template).render(root=root, path=path, **common, **context)

    # The Events chip already covers the plain Event tag, so it isn't listed twice.
    filter_tags = sort_tags({t for p in film_list for t in p.tags} - {"Event"})
    by_next = sorted(film_list, key=lambda p: p.next_showing.starts_at)
    # A page for each festival or series with at least two films here.
    by_series: dict[str, list] = {}
    for page in by_next:
        if page.series:
            by_series.setdefault(page.series, []).append(page)
    series_list = [
        Series(name, slug(name), pages) for name, pages in by_series.items() if len(pages) >= 2
    ]
    series_slugs = {s.name: s.slug for s in series_list}
    out = {
        "index.html": render(
            "index.html",
            "index.html",
            rails=rails(film_list, now, debuts),
            hub=hub(film_list, series_list, now),
            slides=leads(film_list, now) + highlights(film_list, now, count=40),
        ),
        "showtimes/index.html": render(
            "showtimes.html",
            "showtimes/index.html",
            days=days,
            months=calendar_months(days, now),
            filter_tags=filter_tags,
            genres=sorted({g for p in film_list for g in p.genres}),
            current="showtimes",
        ),
    }

    out["films/index.html"] = render(
        "films.html",
        "films/index.html",
        films=by_next,
        filter_tags=filter_tags,
        genres=sorted({g for p in film_list for g in p.genres}),
        current="films",
    )

    for s in series_list:
        path = f"series/{s.slug}/index.html"
        out[path] = render("series.html", path, series=s)

    for page in film_list:
        path = f"films/{page.slug}/index.html"
        page_days = group_days(page.screenings, page_of, films, now)
        out[path] = render(
            "film.html",
            path,
            page=page,
            film=page.film,
            days=page_days,
            runs=theatre_runs(page_days),
            series_slug=series_slugs.get(page.series),
        )

    out["theatres/index.html"] = render("theatres.html", "theatres/index.html", current="theatres")
    out["films.json"] = json.dumps(shelf_index(film_list, now), separators=(",", ":"))
    out["alerts.json"] = json.dumps(alert_feed(film_list), separators=(",", ":"))
    for user in profiles.SYNCED:
        stored = db.load_profile(conn, user)
        if not stored:
            continue
        data = personal.profile_json(stored[0], film_list, now)
        out[f"me/{user}.json"] = json.dumps(data, separators=(",", ":"))
    out["me/index.html"] = render(
        "me.html", "me/index.html", avatars=AVATARS, avatar_credits=avatar_credits()
    )
    out["about/index.html"] = render("about.html", "about/index.html")
    out["privacy/index.html"] = render("privacy.html", "privacy/index.html")
    for venue in VENUES:
        path = f"theatres/{venue.slug}/index.html"
        mine = [s for s in screenings if s.venue == venue.name]
        venue_days = group_days(mine, page_of, films, now)
        out[path] = render(
            "venue.html",
            path,
            venue=venue,
            spec_fields=shown_specs(),
            # What's on the schedule here right now: events and formats from the listings.
            listed=[
                t for t in sort_tags({t for s in mine for t in tags_for(s)}) if kind(t) != "rare"
            ],
            current="theatres",
            days=venue_days,
            months=calendar_months(venue_days, now),
            rails=theatre_rails(venue, film_list, now, debuts),
            prices=venue_prices,
        )

    # GitHub Pages shows 404.html for any missing address, at any depth, so its
    # links use the full site address instead of relative paths.
    site_url = env.globals["site_url"]
    out["404.html"] = render("404.html", "404.html", root=site_url, films=by_next[:6])
    out["sitemap.xml"] = sitemap(site_url, [p for p in out if p.endswith("index.html")])
    # On its own domain the site sits at the root, where crawlers look for this.
    out["robots.txt"] = f"User-agent: *\nAllow: /\n\nSitemap: {site_url}sitemap.xml\n"
    return out


@dataclass
class Hub:
    """The three tiles under the home page's hero."""

    tonight: int
    first: object  # the next showing today, if any
    series: "Series | None"
    special: int


def hub(film_list: list, series_list: list, now: datetime) -> Hub:
    today = now.astimezone(VANCOUVER).date()
    later_today = [
        s
        for p in film_list
        for s in p.screenings
        if s.local_start.date() == today and s.starts_at > now
    ]
    tonight = len({id(p) for p in film_list for s in p.screenings if s in later_today})
    # The festival to point at: on now or starting within two weeks, the biggest first.
    current = [s for s in series_list if s.last >= today and (s.first - today).days <= 14]
    pick = max(current, key=lambda s: (len(s.films), -s.first.toordinal()), default=None)
    # Film prints and events in the coming week.
    special = sum(
        1
        for p in film_list
        if (p.next_showing.local_start.date() - today).days < 7
        and "special" in filter_slugs(p.tags)
    )
    first = min(later_today, key=lambda s: s.starts_at, default=None)
    return Hub(tonight, first, pick, special)


@dataclass
class Series:
    """A festival or series a theatre runs, with the films of it on here."""

    name: str
    slug: str
    films: list

    @property
    def venues(self) -> list:
        return sorted(
            {v.name: v for p in self.films for v in p.venues}.values(), key=lambda v: order(v.name)
        )

    @property
    def first(self):
        return min(s.local_start.date() for p in self.films for s in p.screenings)

    @property
    def last(self):
        return max(s.local_start.date() for p in self.films for s in p.screenings)

    @property
    def dates(self) -> str:
        a, b = self.first, self.last
        return f"{a:%b} {a.day}" if a == b else f"{a:%b} {a.day} to {b:%b} {b.day}"


def sitemap(site_url: str, paths: list[str]) -> str:
    """Every page, so search engines find films and theatres, not just the home page."""
    urls = "".join(
        f"  <url><loc>{site_url}{p.removesuffix('index.html')}</loc></url>\n" for p in sorted(paths)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        f"{urls}</urlset>\n"
    )


# Scrapes run every 4 hours, so 3 failures in a row is about 12 hours of stale listings.
STALE_AFTER = 3


def stats(conn: Connection, now: datetime) -> dict:
    """A summary for checking a deploy at a glance (published as stats.json)."""
    screenings = db.upcoming(conn, now)
    films = db.films_by_key(conn)
    runs = db.load_runs(conn)
    keys = {match_key(s.title, s.year) for s in screenings}
    known = [films[k] for k in keys if k in films]
    per_venue: dict[str, int] = defaultdict(int)
    tag_counts: dict[str, int] = defaultdict(int)
    for s in screenings:
        per_venue[s.venue] += 1
        for t in tags_for(s):
            tag_counts[t] += 1
    return {
        "updated": now.isoformat(),
        "screenings": len(screenings),
        "per_venue": dict(sorted(per_venue.items(), key=lambda item: order(item[0]))),
        "tagged_screenings": dict(sorted(tag_counts.items())),
        "films": len(keys),
        "looked_up": len(known),
        "matched": sum(f.matched for f in known),
        "with_letterboxd_rating": sum(f.letterboxd_rating is not None for f in known),
        "with_letterboxd_count": sum(f.letterboxd_count is not None for f in known),
        "with_imdb_rating": sum(f.imdb_rating is not None for f in known),
        "with_rt_score": sum(f.rt_score is not None for f in known),
        "unmatched": sorted(k for k in keys if k not in films or not films[k].matched),
        "venues": {
            venue: {"last_ok": last_ok.isoformat() if last_ok else None, "failures": failures}
            for venue, (last_ok, failures) in sorted(runs.items(), key=lambda item: order(item[0]))
        },
        # Theatres whose last few scrapes all failed; the Deploy workflow fails on these.
        "stale": [v for v, (_, failures) in runs.items() if failures >= STALE_AFTER],
    }
