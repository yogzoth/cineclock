"""SQLite storage for screenings.

A screening is identified by venue, title and start time. Each scrape replaces
a venue's upcoming screenings: new ones are added, existing ones are updated in
place, and ones the venue no longer lists (cancelled or moved) are removed.
Past screenings are never touched, so they stay as history.
"""

import json
import sqlite3
from dataclasses import asdict, dataclass, fields, replace
from datetime import UTC, date, datetime

from cineclock.films import Film
from cineclock.models import Screening
from cineclock.profiles import Profile

SCHEMA = """
CREATE TABLE IF NOT EXISTS screenings (
    id INTEGER PRIMARY KEY,
    venue TEXT NOT NULL,
    title TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    url TEXT NOT NULL,
    room TEXT,
    info_url TEXT,
    year INTEGER,
    note TEXT,
    poster_url TEXT,
    film_format TEXT,
    series TEXT,
    series_url TEXT,
    imdb_id TEXT,
    synopsis TEXT,
    status TEXT,
    first_seen TEXT,
    UNIQUE (venue, title, starts_at)
);
CREATE INDEX IF NOT EXISTS screenings_starts_at ON screenings (starts_at);

-- How each theatre's last scrapes went, so a broken scraper gets noticed.
CREATE TABLE IF NOT EXISTS venue_runs (
    venue TEXT PRIMARY KEY,
    last_ok TEXT,
    failures INTEGER NOT NULL DEFAULT 0
);

-- Each theatre's ticket prices as last read from its website (prices.py).
CREATE TABLE IF NOT EXISTS prices (
    venue TEXT PRIMARY KEY,
    data TEXT NOT NULL,
    fetched_at TEXT NOT NULL
);

-- Details looked up for each venue title and year. Kept separately from
-- screenings so lookups happen once a week per film, not on every scrape.
CREATE TABLE IF NOT EXISTS films (
    match_key TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    tmdb_id INTEGER,
    imdb_id TEXT,
    title TEXT,
    year INTEGER,
    director TEXT,
    runtime INTEGER,
    overview TEXT,
    genres TEXT,
    poster_url TEXT,
    backdrop_url TEXT,
    letterboxd_url TEXT,
    letterboxd_rating REAL,
    letterboxd_count INTEGER,
    imdb_rating REAL,
    rt_score INTEGER,
    metacritic INTEGER
);

-- A synced Letterboxd member's films, one row per film they've listed or watched.
CREATE TABLE IF NOT EXISTS profile_films (
    user TEXT NOT NULL,
    slug TEXT NOT NULL,
    watchlist INTEGER NOT NULL DEFAULT 0,
    watched INTEGER NOT NULL DEFAULT 0,
    rating INTEGER,
    last_watched TEXT,
    PRIMARY KEY (user, slug)
);

CREATE TABLE IF NOT EXISTS profiles (
    user TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    diary_since TEXT
);

-- What an AI read of each listing found (ai.py): extra tags and the festival
-- it belongs to, if the listing names one. One row per film at a theatre.
CREATE TABLE IF NOT EXISTS ai_notes (
    venue TEXT NOT NULL,
    title TEXT NOT NULL,
    tags TEXT NOT NULL,
    festival TEXT,
    checked TEXT NOT NULL,
    film TEXT,
    film_year INTEGER,
    PRIMARY KEY (venue, title)
);

-- A festival or series' own page at the theatre: its art and blurb (festivals.py).
CREATE TABLE IF NOT EXISTS series_pages (
    url TEXT PRIMARY KEY,
    image TEXT,
    blurb TEXT,
    checked TEXT NOT NULL
);
"""

FIELDS = (
    "venue", "title", "starts_at", "url", "room", "info_url", "year", "note", "poster_url",
    "film_format", "series", "series_url", "imdb_id", "synopsis", "status",
)  # fmt: skip


@dataclass(frozen=True)
class SyncResult:
    added: int
    kept: int
    removed: int
    # True when the scrape found far fewer upcoming shows than are saved, so nothing was deleted.
    held: bool = False


# Below this many saved upcoming shows, a big drop isn't unusual enough to doubt.
HOLD_MINIMUM = 10


# Columns added after the first release. A database saved by an older version
# (like the one the deploy keeps between runs) gets them added on connect.
ADDED_COLUMNS = {
    "screenings": {
        "first_seen": "TEXT",
        "film_format": "TEXT",
        "room": "TEXT",
        "series": "TEXT",
        "series_url": "TEXT",
        "imdb_id": "TEXT",
        "synopsis": "TEXT",
        "status": "TEXT",
    },
    "films": {"letterboxd_count": "INTEGER", "genres": "TEXT", "metacritic": "INTEGER"},
    "ai_notes": {"film": "TEXT", "film_year": "INTEGER"},
}


def connect(path: str = "cineclock.db") -> sqlite3.Connection:
    # The web app may open the connection on one thread and use it on another.
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    upgrade(conn)
    return conn


def upgrade(conn: sqlite3.Connection) -> None:
    for table, columns in ADDED_COLUMNS.items():
        have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    # VIFF's studio was once its own venue; it's now a room of VIFF Centre.
    conn.execute(
        "UPDATE OR IGNORE screenings SET venue = 'VIFF Centre', room = 'viff-lochmaddy' "
        "WHERE venue = 'VIFF Lochmaddy'"
    )
    conn.execute("DELETE FROM screenings WHERE venue = 'VIFF Lochmaddy'")
    conn.commit()


def _utc_text(dt: datetime) -> str:
    # Times are saved as UTC text in one fixed format, so comparing and sorting
    # the text gives the same answer as comparing the times.
    return dt.astimezone(UTC).replace(microsecond=0).isoformat()


def _to_row(s: Screening) -> tuple:
    return (
        s.venue,
        s.title,
        _utc_text(s.starts_at),
        s.url,
        s.room,
        s.info_url,
        s.year,
        s.note,
        s.poster_url,
        s.film_format,
        s.series,
        s.series_url,
        s.imdb_id,
        s.synopsis,
        s.status,
    )


def _from_row(row: sqlite3.Row) -> Screening:
    data = {field: row[field] for field in FIELDS}
    data["starts_at"] = datetime.fromisoformat(data["starts_at"])
    if row["first_seen"]:
        data["first_seen"] = datetime.fromisoformat(row["first_seen"])
    return Screening(**data)


def sync_venue(
    conn: sqlite3.Connection,
    venue: str,
    screenings: list[Screening],
    now: datetime,
    allow_empty: bool = False,
) -> SyncResult:
    if any(s.venue != venue for s in screenings):
        raise ValueError(f"all screenings must belong to {venue}")

    # An empty scrape is far more likely a broken page than a venue with nothing
    # on, so leave the existing listings alone rather than wiping them. The one
    # exception: a scraper covering two screens that found shows for one of them
    # is working, so the other really has nothing on.
    if not screenings and not allow_empty:
        return SyncResult(added=0, kept=0, removed=0)

    cutoff = _utc_text(now)
    scraped = {(s.title, _utc_text(s.starts_at)) for s in screenings}

    # "with conn" makes this one transaction: if anything fails, nothing changes.
    with conn:
        rows = conn.execute(
            "SELECT title, starts_at, room, first_seen FROM screenings WHERE venue = ?", (venue,)
        ).fetchall()
        existing = {(row["title"], row["starts_at"]) for row in rows}
        # A show that vanished while a new title appeared in the same room at the same
        # time was renamed ("Jaws" became "Jaws (35mm)"), not added: keep its first_seen.
        renamed_from = {
            (row["starts_at"], row["room"]): row["first_seen"]
            for row in rows
            if (row["title"], row["starts_at"]) not in scraped
        }

        def first_seen(s: Screening) -> str:
            if (s.title, _utc_text(s.starts_at)) in existing:
                return cutoff
            return renamed_from.get((_utc_text(s.starts_at), s.room)) or cutoff

        conn.executemany(
            f"""
            INSERT INTO screenings ({", ".join(FIELDS)}, first_seen)
            VALUES ({", ".join("?" * (len(FIELDS) + 1))})
            ON CONFLICT (venue, title, starts_at) DO UPDATE SET
                url = excluded.url,
                room = excluded.room,
                info_url = excluded.info_url,
                year = excluded.year,
                note = excluded.note,
                poster_url = excluded.poster_url,
                film_format = excluded.film_format,
                series = excluded.series,
                series_url = excluded.series_url,
                imdb_id = excluded.imdb_id,
                synopsis = excluded.synopsis,
                status = excluded.status,
                -- Keep the date we first saw it; only fill it in for older rows.
                first_seen = COALESCE(screenings.first_seen, excluded.first_seen)
            """,
            [(*_to_row(s), first_seen(s)) for s in screenings],
        )
        gone = {key for key in existing - scraped if key[1] >= cutoff}
        upcoming = sum(1 for key in existing if key[1] >= cutoff)
        # A page that half-loaded looks like a mass cancellation; keep what's saved.
        held = upcoming >= HOLD_MINIMUM and len(gone) > upcoming / 2
        if held:
            gone = set()
        conn.executemany(
            "DELETE FROM screenings WHERE venue = ? AND title = ? AND starts_at = ?",
            [(venue, title, starts_at) for title, starts_at in gone],
        )

    return SyncResult(
        added=len(scraped - existing),
        kept=len(scraped & existing),
        removed=len(gone),
        held=held,
    )


def upcoming(conn: sqlite3.Connection, now: datetime) -> list[Screening]:
    rows = conn.execute(
        "SELECT * FROM screenings WHERE starts_at >= ? ORDER BY starts_at, venue, title",
        (_utc_text(now),),
    )
    notes = load_ai_notes(conn)
    found = []
    for row in rows:
        s = _from_row(row)
        note = notes.get((s.venue, s.title))
        if note:
            tags, festival = note
            s = replace(s, ai_tags=tuple(tags), series=s.series or festival)
        found.append(s)
    return found


def ai_notes_checked(conn: sqlite3.Connection) -> dict[tuple[str, str], datetime]:
    """When each film at each theatre was last read by the AI pass."""
    rows = conn.execute("SELECT venue, title, checked FROM ai_notes")
    return {(r["venue"], r["title"]): datetime.fromisoformat(r["checked"]) for r in rows}


def load_ai_notes(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[list[str], str | None]]:
    rows = conn.execute("SELECT venue, title, tags, festival FROM ai_notes")
    return {(r["venue"], r["title"]): (json.loads(r["tags"]), r["festival"]) for r in rows}


def save_ai_note(
    conn: sqlite3.Connection,
    venue: str,
    title: str,
    tags: list[str],
    festival: str | None,
    now,
    film: str | None = None,
    film_year: int | None = None,
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO ai_notes (venue, title, tags, festival, checked, film, film_year) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (venue, title, json.dumps(tags), festival, _utc_text(now), film, film_year),
    )


def ai_films(conn: sqlite3.Connection) -> dict[str, tuple[str, int | None, datetime]]:
    """The real film the AI found inside an event-style listing title, by that title."""
    rows = conn.execute(
        "SELECT title, film, film_year, checked FROM ai_notes WHERE film IS NOT NULL"
    )
    return {
        r["title"]: (r["film"], r["film_year"], datetime.fromisoformat(r["checked"])) for r in rows
    }


def load_series_pages(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute("SELECT url, image, blurb, checked FROM series_pages")
    return {r["url"]: dict(r) for r in rows}


def save_series_page(
    conn: sqlite3.Connection, url: str, image: str | None, blurb: str | None, now
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO series_pages (url, image, blurb, checked) VALUES (?, ?, ?, ?)",
        (url, image, blurb, _utc_text(now)),
    )


def venue_debuts(conn: sqlite3.Connection) -> dict[str, datetime]:
    """When each theatre was first scraped, from every screening ever stored."""
    rows = conn.execute(
        "SELECT venue, MIN(first_seen) AS debut FROM screenings "
        "WHERE first_seen IS NOT NULL GROUP BY venue"
    )
    return {row["venue"]: datetime.fromisoformat(row["debut"]) for row in rows}


FILM_FIELDS = [f.name for f in fields(Film)]


def get_film(conn: sqlite3.Connection, key: str) -> Film | None:
    row = conn.execute("SELECT * FROM films WHERE match_key = ?", (key,)).fetchone()
    if not row:
        return None
    data = {name: row[name] for name in FILM_FIELDS}
    data["fetched_at"] = datetime.fromisoformat(data["fetched_at"])
    return Film(**data)


def save_film(conn: sqlite3.Connection, film: Film) -> None:
    data = asdict(film)
    data["fetched_at"] = _utc_text(film.fetched_at)
    columns = ", ".join(FILM_FIELDS)
    placeholders = ", ".join(f":{name}" for name in FILM_FIELDS)
    with conn:
        conn.execute(f"INSERT OR REPLACE INTO films ({columns}) VALUES ({placeholders})", data)


def films_by_key(conn: sqlite3.Connection) -> dict[str, Film]:
    rows = conn.execute("SELECT match_key FROM films").fetchall()
    return {row["match_key"]: get_film(conn, row["match_key"]) for row in rows}


def save_profile(conn: sqlite3.Connection, profile: Profile, now: datetime) -> None:
    """Replace everything stored for this member with a fresh read."""
    slugs = profile.watchlist | set(profile.watched)
    rows = [
        (
            profile.user,
            slug,
            int(slug in profile.watchlist),
            int(slug in profile.watched),
            profile.watched.get(slug),
            profile.diary[slug].isoformat() if slug in profile.diary else None,
        )
        for slug in sorted(slugs)
    ]
    since = profile.diary_since.isoformat() if profile.diary_since else None
    with conn:
        conn.execute("DELETE FROM profile_films WHERE user = ?", (profile.user,))
        conn.executemany("INSERT INTO profile_films VALUES (?, ?, ?, ?, ?, ?)", rows)
        conn.execute(
            "INSERT OR REPLACE INTO profiles VALUES (?, ?, ?)",
            (profile.user, _utc_text(now), since),
        )


def load_profile(conn: sqlite3.Connection, user: str) -> tuple[Profile, datetime] | None:
    """The stored profile and when it was read, or None if never synced."""
    head = conn.execute("SELECT * FROM profiles WHERE user = ?", (user,)).fetchone()
    if not head:
        return None
    profile = Profile(user)
    if head["diary_since"]:
        profile.diary_since = date.fromisoformat(head["diary_since"])
    for row in conn.execute("SELECT * FROM profile_films WHERE user = ?", (user,)):
        if row["watchlist"]:
            profile.watchlist.add(row["slug"])
        if row["watched"]:
            profile.watched[row["slug"]] = row["rating"]
        if row["last_watched"]:
            profile.diary[row["slug"]] = date.fromisoformat(row["last_watched"])
    return profile, datetime.fromisoformat(head["fetched_at"])


def record_run(conn: sqlite3.Connection, venue: str, ok: bool, now: datetime) -> None:
    with conn:
        if ok:
            conn.execute(
                "INSERT INTO venue_runs (venue, last_ok, failures) VALUES (?, ?, 0) "
                "ON CONFLICT (venue) DO UPDATE SET last_ok = excluded.last_ok, failures = 0",
                (venue, _utc_text(now)),
            )
        else:
            conn.execute(
                "INSERT INTO venue_runs (venue, failures) VALUES (?, 1) "
                "ON CONFLICT (venue) DO UPDATE SET failures = failures + 1",
                (venue,),
            )


def load_runs(conn: sqlite3.Connection) -> dict[str, tuple[datetime | None, int]]:
    rows = conn.execute("SELECT venue, last_ok, failures FROM venue_runs")
    return {
        row["venue"]: (
            datetime.fromisoformat(row["last_ok"]) if row["last_ok"] else None,
            row["failures"],
        )
        for row in rows
    }


def save_prices(conn: sqlite3.Connection, venue: str, data: dict, now: datetime) -> None:
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO prices VALUES (?, ?, ?)",
            (venue, json.dumps(data), _utc_text(now)),
        )


def load_prices(conn: sqlite3.Connection) -> dict[str, tuple[dict, datetime]]:
    rows = conn.execute("SELECT venue, data, fetched_at FROM prices")
    return {
        row["venue"]: (json.loads(row["data"]), datetime.fromisoformat(row["fetched_at"]))
        for row in rows
    }
