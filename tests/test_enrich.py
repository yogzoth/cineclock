from datetime import UTC, datetime, timedelta

import httpx
import pytest

from cineclock import db
from cineclock.enrich import EnrichResult, enrich
from cineclock.films import Film, match_key
from cineclock.models import Screening

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)


def show(title, year=None):
    return Screening("VIFF Lochmaddy", title, NOW + timedelta(days=1), "https://x", year=year)


@pytest.fixture
def services(fixture_json, fixture_text):
    """Fake TMDB, Letterboxd and OMDb. `down` lists services to fail."""
    state = {"calls": [], "down": set()}

    def handler(request):
        host = request.url.host
        state["calls"].append(request)
        if host in state["down"]:
            return httpx.Response(503)
        if host == "api.themoviedb.org":
            if request.url.path == "/3/search/movie":
                if request.url.params["query"] == "The Thing":
                    return httpx.Response(
                        200, json=fixture_json("enrich/tmdb_search_the_thing.json")
                    )
                return httpx.Response(200, json={"results": []})
            return httpx.Response(200, json=fixture_json("enrich/tmdb_movie_1091.json"))
        if host == "letterboxd.com":
            return httpx.Response(200, text=fixture_text("enrich/letterboxd_the_thing.html"))
        if host == "www.omdbapi.com":
            return httpx.Response(200, json=fixture_json("enrich/omdb_tt0084787.json"))
        return httpx.Response(404)

    state["client"] = httpx.Client(transport=httpx.MockTransport(handler))
    return state


def run(conn, services, now=NOW, omdb_key="o"):
    return enrich(conn, services["client"], now, "k", omdb_key, pause=0)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def test_full_lookup(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    assert run(conn, services) == EnrichResult(looked_up=1, matched=1, failed=0)

    film = db.get_film(conn, match_key("The Thing", 1982))
    assert (film.tmdb_id, film.director, film.runtime) == (1091, "John Carpenter", 109)
    assert film.letterboxd_url == "https://letterboxd.com/film/the-thing/"
    assert (film.letterboxd_rating, film.imdb_rating, film.rt_score) == (4.35, 8.2, 85)
    assert film.letterboxd_count == 1476132
    assert film.metacritic == 57
    assert film.genre_list == ["Horror", "Mystery", "Science Fiction"]
    assert film.poster_url.startswith("https://image.tmdb.org/")


def test_no_match_is_remembered(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("SFMF Series 2")], NOW)
    assert run(conn, services).matched == 0
    film = db.get_film(conn, match_key("SFMF Series 2", None))
    assert film is not None and not film.matched


def test_fresh_results_are_not_looked_up_again(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    run(conn, services)
    before = len(services["calls"])
    assert run(conn, services, now=NOW + timedelta(days=6)).looked_up == 0
    assert len(services["calls"]) == before


def test_found_films_are_not_looked_up_again_for_a_month(conn, services):
    later = NOW + timedelta(days=20)
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    run(conn, services)
    db.sync_venue(
        conn,
        "VIFF Lochmaddy",
        [
            Screening(
                "VIFF Lochmaddy", "The Thing", later + timedelta(days=1), "https://x", year=1982
            )
        ],
        later,
    )
    assert run(conn, services, now=later).looked_up == 0


def test_unfound_titles_are_retried_after_a_week(conn, services):
    later = NOW + timedelta(days=8)
    db.sync_venue(conn, "VIFF Lochmaddy", [show("SFMF Series 2")], NOW)
    run(conn, services)
    db.sync_venue(
        conn,
        "VIFF Lochmaddy",
        [Screening("VIFF Lochmaddy", "SFMF Series 2", later + timedelta(days=1), "https://x")],
        later,
    )
    assert run(conn, services, now=later).looked_up == 1


def test_month_old_results_are_refreshed(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    run(conn, services)
    later = NOW + timedelta(days=31)
    assert run(conn, services, now=later).looked_up == 0  # the show itself is past by then
    db.sync_venue(
        conn,
        "VIFF Lochmaddy",
        [
            Screening(
                "VIFF Lochmaddy", "The Thing", later + timedelta(days=1), "https://x", year=1982
            )
        ],
        later,
    )
    assert run(conn, services, now=later).looked_up == 1


def test_letterboxd_down_keeps_the_rest(conn, services):
    services["down"].add("letterboxd.com")
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    run(conn, services)
    film = db.get_film(conn, match_key("The Thing", 1982))
    assert film.matched and film.letterboxd_rating is None and film.imdb_rating == 8.2
    assert film.letterboxd_link == "https://letterboxd.com/tmdb/1091/"


def test_omdb_skipped_without_key(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    run(conn, services, omdb_key=None)
    assert not any(c.url.host == "www.omdbapi.com" for c in services["calls"])
    assert db.get_film(conn, match_key("The Thing", 1982)).imdb_rating is None


def test_tmdb_down_is_retried_next_run(conn, services):
    services["down"].add("api.themoviedb.org")
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    assert run(conn, services) == EnrichResult(looked_up=0, matched=0, failed=1)
    assert db.get_film(conn, match_key("The Thing", 1982)) is None
    services["down"].clear()
    assert run(conn, services).matched == 1


def test_no_tmdb_key_does_nothing(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    assert enrich(conn, services["client"], NOW, None) == EnrichResult(0, 0, 0)
    assert services["calls"] == []


def test_same_film_at_two_venues_is_looked_up_once(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    db.sync_venue(
        conn,
        "The Rio",
        [Screening("The Rio", "The Thing", NOW + timedelta(days=2), "https://y", year=1982)],
        NOW,
    )
    assert run(conn, services).looked_up == 1


def test_films_saved_before_rating_counts_get_one_early_refresh(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    old = Film(
        match_key("The Thing", 1982), NOW, tmdb_id=1091, letterboxd_rating=4.35, genres="Horror"
    )
    db.save_film(conn, old)
    assert run(conn, services, now=NOW + timedelta(days=1)).looked_up == 1
    assert db.get_film(conn, match_key("The Thing", 1982)).letterboxd_count == 1476132
    assert run(conn, services, now=NOW + timedelta(days=2)).looked_up == 0


def test_failed_refresh_keeps_last_ratings(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    run(conn, services)
    services["down"].update({"letterboxd.com", "www.omdbapi.com"})
    later = NOW + timedelta(days=31)
    db.sync_venue(
        conn,
        "VIFF Lochmaddy",
        [
            Screening(
                "VIFF Lochmaddy", "The Thing", later + timedelta(days=1), "https://x", year=1982
            )
        ],
        later,
    )
    assert run(conn, services, now=later).looked_up == 1
    film = db.get_film(conn, match_key("The Thing", 1982))
    assert (film.letterboxd_rating, film.letterboxd_count, film.imdb_rating, film.rt_score) == (
        4.35,
        1476132,
        8.2,
        85,
    )
    assert film.metacritic == 57
    assert film.fetched_at == later


def test_films_saved_before_genres_get_one_early_refresh(conn, services):
    db.sync_venue(conn, "VIFF Lochmaddy", [show("The Thing", 1982)], NOW)
    db.save_film(conn, Film(match_key("The Thing", 1982), NOW, tmdb_id=1091))
    assert run(conn, services, now=NOW + timedelta(days=1)).looked_up == 1
    assert (
        db.get_film(conn, match_key("The Thing", 1982)).genres == "Horror, Mystery, Science Fiction"
    )


def test_a_linked_imdb_id_is_used_for_the_lookup(monkeypatch):
    from cineclock.enrich import tmdb

    asked = []
    monkeypatch.setattr(
        tmdb, "find", lambda client, key, title, year, imdb_id=None: asked.append(imdb_id)
    )
    conn = db.connect(":memory:")
    now = datetime(2026, 10, 20, tzinfo=UTC)
    queen = Screening(
        "The Park", "Jim Queen", now + timedelta(days=3), "https://t/1", imdb_id="tt36955518"
    )
    db.sync_venue(conn, "The Park", [queen], now)
    enrich(conn, httpx.Client(), now, "key", pause=0)
    assert asked == ["tt36955518"]


def test_an_unmatched_event_title_is_searched_by_the_film_inside_it(monkeypatch):
    from cineclock.enrich import letterboxd, tmdb

    monkeypatch.setattr(letterboxd, "find_tmdb_id", lambda *args: None)

    asked = []
    monkeypatch.setattr(
        tmdb, "find", lambda client, key, title, year, imdb_id=None: asked.append((title, year))
    )
    conn = db.connect(":memory:")
    now = datetime(2026, 10, 21, tzinfo=UTC)
    event = (
        "Food Stash Foundation 10 Year Anniversary Celebration: Just Eat It - A Food Waste Story"
    )
    db.sync_venue(
        conn,
        "The Park",
        [Screening("The Park", event, now + timedelta(days=3), "https://t/1")],
        now,
    )
    db.save_film(conn, Film(match_key(event, None), now - timedelta(days=1)))  # unmatched
    db.save_ai_note(conn, "The Park", event, [], None, now, "Just Eat It: A Food Waste Story", 2014)
    enrich(conn, httpx.Client(), now, "key", pause=0)
    assert asked == [("Just Eat It: A Food Waste Story", 2014)]
    enrich(conn, httpx.Client(), now + timedelta(hours=4), "key", pause=0)
    assert len(asked) == 1  # tried once for this hint, not every run


def test_letterboxd_page_gives_the_tmdb_id_when_search_cant(fixture_text):
    from cineclock.enrich import letterboxd

    page = fixture_text("enrich/letterboxd_begotten.html")
    assert letterboxd.tmdb_id_on_page(page, 1989) == 1483
    assert letterboxd.tmdb_id_on_page(page, 1990) == 1483
    assert letterboxd.tmdb_id_on_page(page, 2005) is None  # a different film

    def handler(request):
        if request.url.path == "/film/begotten/":
            return httpx.Response(200, text=page)
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert letterboxd.find_tmdb_id(client, "Begotten", 1989, "begotten") == 1483
