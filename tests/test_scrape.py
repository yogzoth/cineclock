from datetime import UTC, datetime, timedelta

import pytest

from cineclock import db, scrape
from cineclock.models import Screening
from cineclock.scrapers import cinematheque, clean_summary, park, rio, viff

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)


def show(venue, title, days=1):
    return Screening(venue, title, NOW + timedelta(days=days), f"https://x/{title}")


def run(conn, monkeypatch, scrapers):
    monkeypatch.setattr(scrape, "SCRAPERS", scrapers)
    scrape.run_scrapers(conn, client=None, now=NOW)


def test_one_broken_venue_does_not_stop_the_others(monkeypatch, capsys):
    conn = db.connect(":memory:")

    def broken(client, now):
        raise RuntimeError("site is down")

    def working(client, now):
        return [show("The Rio", "Stalker")]

    run(conn, monkeypatch, [(broken, ("The Park",)), (working, ("The Rio",))])
    output = capsys.readouterr().out
    assert "The Park: failed (site is down)" in output
    assert "The Rio: 1 new, 0 kept, 0 removed" in output
    assert [s.title for s in db.upcoming(conn, NOW)] == ["Stalker"]


def test_two_screen_scraper_syncs_each_screen(monkeypatch):
    conn = db.connect(":memory:")
    venues = ("VIFF Centre", "VIFF Lochmaddy")

    def both(client, now):
        return [show("VIFF Centre", "Cure"), show("VIFF Lochmaddy", "Pulse")]

    def centre_only(client, now):
        return [show("VIFF Centre", "Cure")]

    run(conn, monkeypatch, [(both, venues)])
    run(conn, monkeypatch, [(centre_only, venues)])
    # The scraper worked, so Lochmaddy really has nothing on now.
    assert [(s.venue, s.title) for s in db.upcoming(conn, NOW)] == [("VIFF Centre", "Cure")]


def test_empty_scraper_keeps_everything(monkeypatch):
    conn = db.connect(":memory:")
    venues = ("VIFF Centre", "VIFF Lochmaddy")
    run(conn, monkeypatch, [(lambda client, now: [show("VIFF Lochmaddy", "Pulse")], venues)])
    run(conn, monkeypatch, [(lambda client, now: [], venues)])
    assert [s.title for s in db.upcoming(conn, NOW)] == ["Pulse"]


def test_old_combined_viff_rows_move_to_the_right_screen(monkeypatch):
    # Before the split, Lochmaddy shows were saved under "VIFF Centre".
    conn = db.connect(":memory:")
    db.sync_venue(conn, "VIFF Centre", [show("VIFF Centre", "Pulse")], NOW)
    run(
        conn,
        monkeypatch,
        [
            (
                lambda client, now: [show("VIFF Lochmaddy", "Pulse")],
                ("VIFF Centre", "VIFF Lochmaddy"),
            )
        ],
    )
    assert [s.venue for s in db.upcoming(conn, NOW)] == ["VIFF Lochmaddy"]


def test_profiles_sync_once_a_day_and_keep_the_last_copy_on_failure(monkeypatch):
    from cineclock import profiles
    from cineclock.profiles import Profile

    conn = db.connect(":memory:")
    calls = []

    def fetch(client, user, pause):
        calls.append(user)
        if len(calls) == 2:
            raise RuntimeError("letterboxd down")
        return Profile(user, watchlist={"jaws"})

    monkeypatch.setattr(profiles, "SYNCED", ["me"])
    monkeypatch.setattr(profiles, "fetch", fetch)
    scrape.sync_profiles(conn, None, NOW)
    scrape.sync_profiles(conn, None, NOW + timedelta(hours=2))  # too soon, skipped
    scrape.sync_profiles(conn, None, NOW + timedelta(days=1))  # fails
    assert calls == ["me", "me"]
    assert db.load_profile(conn, "me")[0].watchlist == {"jaws"}


# Theatre descriptions, used on film pages when TMDB has no synopsis


@pytest.mark.parametrize(
    "text, expected",
    [
        (
            "An incisive look behind the legend of Elon Musk. MUSK (Alex Gibney, 2026 / PG) More",
            "An incisive look behind the legend of Elon Musk.",
        ),
        (
            "A deep dive into Halloween classics continues. Craven's slasher is sly ... Read more",
            "A deep dive into Halloween classics continues.",
        ),
        (
            "John Carpenter's The Thing is a bone fide arctic chiller. Researchers find something…",
            "John Carpenter's The Thing is a bone fide arctic chiller.",
        ),
        ("Too short.", None),
        (None, None),
    ],
)
def test_clean_summary(text, expected):
    assert clean_summary(text) == expected


def test_each_theatre_page_gives_a_synopsis(fixture_text):
    musk = rio.parse_film_page(fixture_text("rio/movie_musk.html"))
    assert musk.synopsis.startswith("An incisive look behind the legend of Elon Musk")
    assert "235 mins" not in musk.synopsis
    dark = cinematheque.parse_film_page(fixture_text("cinematheque/film_daughters-darkness.html"))
    assert dark.synopsis.startswith("A sequined Delphine Seyrig")
    thing = viff.parse_film_page(fixture_text("viff/film_the-thing.html"))
    assert thing.synopsis == "John Carpenter's The Thing is a bone fide arctic chiller."
    devils = park.parse_film_page(fixture_text("park/barker_movie_ken-russells-the-devils.html"))
    # This page's summary opens with a review quote; the synopsis skips it.
    assert devils.synopsis.startswith("An enduring masterpiece of cinema")


def test_failures_are_counted_until_a_scrape_works(monkeypatch):
    conn = db.connect(":memory:")

    def broken(client, now):
        raise RuntimeError("site is down")

    for _ in range(3):
        run(conn, monkeypatch, [(broken, ("The Park",))])
    assert db.load_runs(conn)["The Park"] == (None, 3)

    run(conn, monkeypatch, [(lambda client, now: [show("The Park", "Jaws")], ("The Park",))])
    assert db.load_runs(conn)["The Park"] == (NOW, 0)


def test_an_empty_scrape_counts_as_a_failure(monkeypatch):
    conn = db.connect(":memory:")
    run(conn, monkeypatch, [(lambda client, now: [], ("The Rio",))])
    assert db.load_runs(conn)["The Rio"] == (None, 1)


def test_a_sudden_mass_disappearance_deletes_nothing(monkeypatch, capsys):
    conn = db.connect(":memory:")
    titles = [f"Film {i}" for i in range(20)]
    full = [show("The Cinematheque", t) for t in titles]
    run(conn, monkeypatch, [(lambda client, now: full, ("The Cinematheque",))])

    # The page half-loads: 6 of 20 shows come back.
    run(conn, monkeypatch, [(lambda client, now: full[:6], ("The Cinematheque",))])
    assert len(db.upcoming(conn, NOW)) == 20
    assert "none were deleted" in capsys.readouterr().out
    assert db.load_runs(conn)["The Cinematheque"][1] == 1

    # A normal cancellation still goes through.
    run(conn, monkeypatch, [(lambda client, now: full[1:], ("The Cinematheque",))])
    assert len(db.upcoming(conn, NOW)) == 19
