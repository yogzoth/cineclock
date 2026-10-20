"""Look up details for every film that's showing: poster, synopsis, links and ratings.

TMDB finds the film. Letterboxd gives its page and rating. OMDb gives the IMDb
and Rotten Tomatoes ratings. Results are cached for a week per film, and a
film that couldn't be found isn't searched again until then either.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from cineclock import db
from cineclock.enrich import letterboxd, omdb, tmdb
from cineclock.films import Film, match_key, slugify

REFRESH_MATCHED = timedelta(days=30)
RETRY_UNMATCHED = timedelta(days=2)
# When matching gets better, every film that didn't match before this is tried
# again on the next run. Move it forward after changing how films are found.
MATCHING_CHANGED = datetime(2026, 10, 20, 21, 0, tzinfo=UTC)


@dataclass(frozen=True)
class EnrichResult:
    looked_up: int
    matched: int
    failed: int


def needs_backfill(film: Film) -> bool:
    """Films saved before rating counts or genres were collected get one early refresh."""
    missing_count = film.letterboxd_rating is not None and film.letterboxd_count is None
    return missing_count or (film.matched and film.genres is None)


def lookup_film(
    client: httpx.Client,
    title: str,
    year: int | None,
    now: datetime,
    tmdb_key: str,
    omdb_key: str | None,
    pause: float,
    previous: Film | None = None,
    imdb_id: str | None = None,
    search: tuple[str, int | None] | None = None,
) -> Film:
    """`search` is the film's own title and year when the listing's title wraps it
    in an event name; the result is still saved under the listing's title."""
    search_title, search_year = search or (title, year)
    found = tmdb.find(client, tmdb_key, search_title, search_year or year, imdb_id)
    if not found and (search_year or year):
        try:
            tmdb_id = letterboxd.find_tmdb_id(
                client, search_title, search_year or year, slugify(search_title)
            )
        except httpx.HTTPError as error:
            print(f"::warning::Letterboxd lookup for {search_title}: {error}")
            tmdb_id = None
        if tmdb_id:
            found = tmdb.details(client, tmdb_key, tmdb_id)
        print(f"::notice::{search_title} ({search_year or year}): Letterboxd found {tmdb_id}")
    if not found:
        return Film(match_key=match_key(title, year), fetched_at=now)

    # Letterboxd and OMDb are extras: if either is down, keep the TMDB match,
    # and keep last time's ratings for the same film rather than blanking them.
    same_film = previous if previous and previous.tmdb_id == found["tmdb_id"] else None
    lb = letterboxd.LetterboxdFilm(
        *(
            (same_film.letterboxd_url, same_film.letterboxd_rating, same_film.letterboxd_count)
            if same_film
            else ()
        )
    )
    scores = (
        omdb.Scores(same_film.imdb_rating, same_film.rt_score, same_film.metacritic)
        if same_film
        else omdb.Scores()
    )
    try:
        time.sleep(pause)  # Be gentle with Letterboxd, which has no API.
        lb = letterboxd.lookup(client, found["tmdb_id"])
    except httpx.HTTPError:
        pass
    if omdb_key and found["imdb_id"]:
        try:
            scores = omdb.lookup(client, omdb_key, found["imdb_id"])
        except httpx.HTTPError:
            pass

    return Film(
        match_key=match_key(title, year),
        fetched_at=now,
        letterboxd_url=lb.url,
        letterboxd_rating=lb.rating,
        letterboxd_count=lb.rating_count,
        imdb_rating=scores.imdb,
        rt_score=scores.rt,
        metacritic=scores.metacritic,
        **found,
    )


def enrich(
    conn,
    client: httpx.Client,
    now: datetime,
    tmdb_key: str | None,
    omdb_key: str | None = None,
    pause: float = 0.5,
) -> EnrichResult:
    if not tmdb_key:
        return EnrichResult(looked_up=0, matched=0, failed=0)

    # A theatre page that links the film's IMDb entry gives an exact match.
    imdb_ids = {(s.title, s.year): s.imdb_id for s in db.upcoming(conn, now) if s.imdb_id}
    # "Food Stash Foundation 10 Year Anniversary: Just Eat It - A Food Waste Story"
    # is "Just Eat It: A Food Waste Story": the AI pass names the film inside.
    hints = db.ai_films(conn)
    wanted = {(s.title, s.year) for s in db.upcoming(conn, now)}
    looked_up = matched = failed = 0
    for title, year in sorted(wanted, key=lambda item: (item[0], item[1] or 0)):
        cached = db.get_film(conn, match_key(title, year))
        hint = hints.get(title)
        # An unmatched film is tried again as soon as there's something new to
        # try it with: an IMDb link, or a film the AI found in its title.
        new_lead = (
            cached
            and not cached.matched
            and (
                ((title, year) in imdb_ids and now - cached.fetched_at > timedelta(days=1))
                or (hint and hint[2] > cached.fetched_at)
                or cached.fetched_at < MATCHING_CHANGED
            )
        )
        if cached and not needs_backfill(cached) and not new_lead:
            wait = REFRESH_MATCHED if cached.matched else RETRY_UNMATCHED
            if now - cached.fetched_at < wait:
                continue
        try:
            film = lookup_film(
                client,
                title,
                year,
                now,
                tmdb_key,
                omdb_key,
                pause,
                cached,
                imdb_ids.get((title, year)),
                hint[:2] if hint else None,
            )
        except httpx.HTTPError:
            # Not saved, so it's retried on the next run.
            failed += 1
            continue
        db.save_film(conn, film)
        looked_up += 1
        matched += film.matched
    return EnrichResult(looked_up=looked_up, matched=matched, failed=failed)
