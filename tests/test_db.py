import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from cineclock import db
from cineclock.models import VANCOUVER, Screening

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)


def show(title, hours_from_now, venue="The Rio", **extra):
    return Screening(
        venue=venue,
        title=title,
        starts_at=NOW + timedelta(hours=hours_from_now),
        url=f"https://example.com/{title}",
        **extra,
    )


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def test_saved_screenings_come_back_unchanged(conn):
    original = show("Stalker", 10, year=1979, note="35mm", poster_url="https://x/p.jpg")
    db.sync_venue(conn, "The Rio", [original], NOW)
    assert db.upcoming(conn, NOW) == [original]


def test_times_come_back_as_utc(conn):
    db.sync_venue(conn, "The Rio", [show("Stalker", 10)], NOW)
    assert db.upcoming(conn, NOW)[0].starts_at.tzinfo == UTC


def test_rerunning_the_same_scrape_does_not_duplicate(conn):
    shows = [show("Stalker", 10), show("Mirror", 20)]
    first = db.sync_venue(conn, "The Rio", shows, NOW)
    second = db.sync_venue(conn, "The Rio", shows, NOW)
    assert first == db.SyncResult(added=2, kept=0, removed=0)
    assert second == db.SyncResult(added=0, kept=2, removed=0)
    assert len(db.upcoming(conn, NOW)) == 2


def test_changed_details_update_in_place(conn):
    db.sync_venue(conn, "The Rio", [show("Stalker", 10)], NOW)
    db.sync_venue(
        conn, "The Rio", [show("Stalker", 10, year=1979, poster_url="https://x/p.jpg")], NOW
    )
    [saved] = db.upcoming(conn, NOW)
    assert saved.year == 1979
    assert saved.poster_url == "https://x/p.jpg"


def test_screening_dropped_by_venue_is_removed(conn):
    db.sync_venue(conn, "The Rio", [show("Stalker", 10), show("Mirror", 20)], NOW)
    result = db.sync_venue(conn, "The Rio", [show("Stalker", 10)], NOW)
    assert result.removed == 1
    assert [s.title for s in db.upcoming(conn, NOW)] == ["Stalker"]


def test_moved_screening_replaces_the_old_time(conn):
    db.sync_venue(conn, "The Rio", [show("Stalker", 10)], NOW)
    result = db.sync_venue(conn, "The Rio", [show("Stalker", 12)], NOW)
    assert result == db.SyncResult(added=1, kept=0, removed=1)
    assert [s.starts_at for s in db.upcoming(conn, NOW)] == [NOW + timedelta(hours=12)]


def test_past_screenings_are_kept_as_history(conn):
    db.sync_venue(
        conn, "The Rio", [show("Stalker", -5), show("Mirror", 10)], NOW - timedelta(days=1)
    )
    db.sync_venue(conn, "The Rio", [show("Mirror", 10)], NOW)
    count = conn.execute("SELECT COUNT(*) FROM screenings").fetchone()[0]
    assert count == 2


def test_other_venues_are_left_alone(conn):
    db.sync_venue(conn, "VIFF Centre", [show("Mirror", 10, venue="VIFF Centre")], NOW)
    db.sync_venue(conn, "The Rio", [show("Stalker", 10)], NOW)
    db.sync_venue(conn, "The Rio", [show("Solaris", 30)], NOW)
    assert {s.title for s in db.upcoming(conn, NOW)} == {"Mirror", "Solaris"}


def test_empty_scrape_keeps_existing_listings(conn):
    db.sync_venue(conn, "The Rio", [show("Stalker", 10)], NOW)
    result = db.sync_venue(conn, "The Rio", [], NOW)
    assert result == db.SyncResult(added=0, kept=0, removed=0)
    assert len(db.upcoming(conn, NOW)) == 1


def test_screening_from_another_venue_is_rejected(conn):
    with pytest.raises(ValueError):
        db.sync_venue(conn, "The Rio", [show("Mirror", 10, venue="VIFF Centre")], NOW)


def test_failed_sync_changes_nothing(conn):
    db.sync_venue(conn, "The Rio", [show("Stalker", 10)], NOW)
    # A missing URL breaks the database's NOT NULL rule partway through the save.
    broken = replace(show("Mirror", 20), url=None)
    with pytest.raises(sqlite3.IntegrityError):
        db.sync_venue(conn, "The Rio", [show("Solaris", 30), broken], NOW)
    assert [s.title for s in db.upcoming(conn, NOW)] == ["Stalker"]


def test_upcoming_is_sorted_and_skips_the_past(conn):
    db.sync_venue(
        conn,
        "The Rio",
        [show("Late", 30), show("Gone", -2), show("Early", 5)],
        NOW - timedelta(days=1),
    )
    assert [s.title for s in db.upcoming(conn, NOW)] == ["Early", "Late"]


def test_now_in_any_time_zone_gives_the_same_answer(conn):
    db.sync_venue(conn, "The Rio", [show("Stalker", 1)], NOW)
    assert db.upcoming(conn, NOW.astimezone(VANCOUVER)) == db.upcoming(conn, NOW)


def test_database_file_persists(tmp_path):
    path = str(tmp_path / "cineclock.db")
    conn = db.connect(path)
    db.sync_venue(conn, "The Rio", [show("Stalker", 10)], NOW)
    conn.close()
    reopened = db.connect(path)
    assert [s.title for s in db.upcoming(reopened, NOW)] == ["Stalker"]
    reopened.close()


def test_first_seen_is_kept_on_later_syncs(conn):
    db.sync_venue(conn, "The Rio", [show("Stalker", 30)], NOW)
    db.sync_venue(
        conn, "The Rio", [show("Stalker", 30), show("Mirror", 40)], NOW + timedelta(days=1)
    )
    seen = {s.title: s.first_seen for s in db.upcoming(conn, NOW)}
    assert seen == {"Stalker": NOW, "Mirror": NOW + timedelta(days=1)}


def test_old_database_gets_new_columns(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE screenings (
            id INTEGER PRIMARY KEY, venue TEXT NOT NULL, title TEXT NOT NULL,
            starts_at TEXT NOT NULL, url TEXT NOT NULL, info_url TEXT, year INTEGER,
            note TEXT, poster_url TEXT, UNIQUE (venue, title, starts_at)
        );
        INSERT INTO screenings (venue, title, starts_at, url)
            VALUES ('The Rio', 'Stalker', '2026-10-19T08:00:00+00:00', 'https://x');
    """)
    old.close()
    conn = db.connect(path)
    [saved] = db.upcoming(conn, NOW)
    assert saved.title == "Stalker" and saved.first_seen is None
    # Syncing fills in the missing date rather than leaving it blank forever
    db.sync_venue(conn, "The Rio", [saved], NOW)
    assert db.upcoming(conn, NOW)[0].first_seen == NOW
    conn.close()


def test_old_viff_lochmaddy_rows_become_a_room_of_viff_centre(tmp_path):
    path = str(tmp_path / "old.db")
    old = sqlite3.connect(path)
    old.executescript("""
        CREATE TABLE screenings (
            id INTEGER PRIMARY KEY, venue TEXT NOT NULL, title TEXT NOT NULL,
            starts_at TEXT NOT NULL, url TEXT NOT NULL, info_url TEXT, year INTEGER,
            note TEXT, poster_url TEXT, UNIQUE (venue, title, starts_at)
        );
        INSERT INTO screenings (venue, title, starts_at, url) VALUES
            ('VIFF Lochmaddy', 'Pulse', '2026-10-19T08:00:00+00:00', 'https://x'),
            ('VIFF Lochmaddy', 'Cure', '2026-10-20T08:00:00+00:00', 'https://y'),
            ('VIFF Centre', 'Cure', '2026-10-20T08:00:00+00:00', 'https://y');
    """)
    old.close()
    conn = db.connect(path)
    saved = [(s.venue, s.title, s.room) for s in db.upcoming(conn, NOW)]
    # The Cure row already existed under VIFF Centre, so the duplicate is dropped
    assert saved == [("VIFF Centre", "Pulse", "viff-lochmaddy"), ("VIFF Centre", "Cure", None)]
    conn.close()


def test_profile_round_trip_replaces_the_old_copy(conn):
    from datetime import date

    from cineclock.profiles import Profile

    first = Profile("me", watchlist={"jaws", "alien"}, watched={"alien": 8})
    db.save_profile(conn, first, NOW)
    second = Profile(
        "me",
        watchlist={"jaws"},
        watched={"alien": 9, "heat": None},
        diary={"heat": date(2026, 10, 1)},
        diary_since=date(2026, 8, 1),
    )
    db.save_profile(conn, second, NOW)
    loaded, fetched = db.load_profile(conn, "me")
    assert loaded == second and fetched == NOW
    assert db.load_profile(conn, "someone-else") is None


def test_a_renamed_show_keeps_when_it_was_first_seen():
    conn = db.connect(":memory:")
    first = NOW
    later = NOW + timedelta(hours=4)
    start = NOW + timedelta(days=3)
    db.sync_venue(conn, "The Rio", [Screening("The Rio", "Jaws", start, "https://x")], first)
    renamed = Screening("The Rio", "Jaws in 35mm", start, "https://x")
    other = Screening("The Rio", "Alien", start + timedelta(hours=3), "https://y")
    db.sync_venue(conn, "The Rio", [renamed, other], later)
    seen = {s.title: s.first_seen for s in db.upcoming(conn, NOW)}
    assert seen == {"Jaws in 35mm": first, "Alien": later}


def test_imdb_and_series_links_are_kept(conn):
    show = Screening(
        "The Park", "Jim Queen", datetime(2026, 11, 1, 3, tzinfo=UTC), "https://t/1",
        imdb_id="tt36955518", series_url="https://x/series/a",
    )  # fmt: skip
    db.sync_venue(conn, "The Park", [show], datetime(2026, 10, 20, tzinfo=UTC))
    [back] = db.upcoming(conn, datetime(2026, 10, 20, tzinfo=UTC))
    assert back.imdb_id == "tt36955518" and back.series_url == "https://x/series/a"
