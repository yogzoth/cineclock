import httpx

from cineclock.models import VANCOUVER
from cineclock.scrapers import park

FEED = "park/barker_listings.json"


def test_feed_gives_films_and_ticketed_events_but_not_private_bookings(fixture_json):
    screenings = park.parse_listings(fixture_json(FEED))
    titles = {s.title for s in screenings}
    assert "Altered States" in titles
    assert "The Saturday Morning All-You-Can-Eat Cereal Cartoon Party!" in titles
    assert "Inspire Film Fest" in titles
    assert "Private Event" not in titles
    assert all(s.venue == "The Park" and s.room == "park-main" for s in screenings)


def test_times_are_the_showtime_not_doors(fixture_json):
    # The ticketing page said 5:00 pm (doors); the feed has the 6:00 pm showtime.
    [food] = [s for s in park.parse_listings(fixture_json(FEED)) if s.title.startswith("Food")]
    local = food.starts_at.astimezone(VANCOUVER)
    assert (local.month, local.day, local.hour, local.minute) == (11, 10, 18, 0)


def test_notes_come_off_titles(fixture_json):
    dune = [s for s in park.parse_listings(fixture_json(FEED)) if s.title == "Dune: Part Three"]
    assert {s.note for s in dune} >= {"Presented in 70mm"}


def test_film_pages_give_year_poster_and_imdb_id(fixture_text):
    states = park.parse_film_page(fixture_text("park/barker_movie_altered-states.html"))
    assert states.year == 1980 and states.imdb_id == "tt0080360"
    assert states.poster_url.startswith("https://www.theparktheatre.ca/")
    # No year anywhere on the page, but the IMDb link still pins the film down.
    queen = park.parse_film_page(fixture_text("park/barker_movie_jim-queen.html"))
    assert queen.year is None and queen.imdb_id == "tt36955518"
    assert queen.synopsis.startswith("Handsome, shallow")


def test_scrape_reads_the_feed_then_each_page_once(fixture_json, fixture_text):
    asked = []

    def handler(request):
        asked.append(request.url.path)
        if request.url.path.startswith("/wp-json/"):
            return httpx.Response(200, json=fixture_json(FEED))
        if request.url.path == "/movie/altered-states/":
            return httpx.Response(200, text=fixture_text("park/barker_movie_altered-states.html"))
        return httpx.Response(404)

    from datetime import UTC, datetime

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        found = park.scrape(client, now=datetime(2026, 10, 20, 12, tzinfo=UTC))
    states = [s for s in found if s.title == "Altered States"]
    assert states and all(s.year == 1980 and s.imdb_id == "tt0080360" for s in states)
    assert asked.count("/movie/altered-states/") == 1
