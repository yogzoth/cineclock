from datetime import UTC, datetime

import httpx
import pytest

from cineclock.models import VANCOUVER
from cineclock.scrapers import FilmDetails, cinematheque


def local(*args):
    return datetime(*args, tzinfo=VANCOUVER)


@pytest.mark.parametrize(
    "clock, ampm, expected",
    [
        ("10:30", "am", local(2026, 10, 18, 10, 30)),
        ("12:00", "pm", local(2026, 10, 18, 12, 0)),
        ("12:15", "am", local(2026, 10, 18, 0, 15)),
        ("8:40", "pm", local(2026, 10, 18, 20, 40)),
        ("8:40", "", None),
        ("TBA", "pm", None),
    ],
)
def test_parse_start(clock, ampm, expected):
    assert cinematheque.parse_start("18", "October", "2026", clock, ampm) == expected


def test_parse_real_calendar(fixture_text):
    screenings = cinematheque.parse_calendar(fixture_text("cinematheque/calendar.html"))
    # 158 entries, minus 2 rentals, the AGM notice and 12 closed days
    assert len(screenings) == 143
    assert all(s.venue == "The Cinematheque" for s in screenings)


def test_real_calendar_fields(fixture_text):
    screenings = cinematheque.parse_calendar(fixture_text("cinematheque/calendar.html"))
    pulse = next(s for s in screenings if s.title == "Pulse" and s.local_start.day == 18)
    assert pulse.local_start == local(2026, 10, 18, 20, 40)
    assert pulse.info_url == "https://thecinematheque.ca/films/2026/pulse"
    assert pulse.url.startswith("https://tickets.thecinematheque.ca/websales/")


def test_calendar_crosses_into_next_year(fixture_text):
    screenings = cinematheque.parse_calendar(fixture_text("cinematheque/calendar.html"))
    assert max(s.local_start for s in screenings).year == 2027


def test_missing_calendar_gives_nothing():
    assert cinematheque.parse_calendar("<html>maintenance</html>") == []


def test_parse_film_page(fixture_text):
    details = cinematheque.parse_film_page(
        fixture_text("cinematheque/film_daughters-darkness.html")
    )
    assert details.year == 1971
    assert details.poster_url.endswith("/DaughtersOfDarkness-1.jpg")


def test_shorts_program_has_no_year(fixture_text):
    details = cinematheque.parse_film_page(fixture_text("cinematheque/film_sfmf-series-2.html"))
    assert details.year is None
    assert details.poster_url is not None


def test_parse_film_page_without_details():
    assert cinematheque.parse_film_page("<html></html>") == FilmDetails()


def test_scrape_adds_details_and_drops_past(fixture_text):
    def handler(request):
        if str(request.url) == cinematheque.CALENDAR_URL:
            return httpx.Response(200, text=fixture_text("cinematheque/calendar.html"))
        if request.url.path == "/films/2026/daughters-darkness":
            return httpx.Response(
                200, text=fixture_text("cinematheque/film_daughters-darkness.html")
            )
        return httpx.Response(404)

    now = datetime(2026, 10, 18, 8, 50, tzinfo=UTC)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        screenings = cinematheque.scrape(client, now=now)

    assert all(s.starts_at >= now for s in screenings)
    daughters = [s for s in screenings if s.title == "Daughters of Darkness"]
    assert daughters and all(s.year == 1971 for s in daughters)


def test_film_page_format(fixture_text):
    details = cinematheque.parse_film_page(
        fixture_text("cinematheque/film_daughters-darkness.html")
    )
    assert details.film_format == "DCP"


def test_shorts_program_has_no_format(fixture_text):
    assert (
        cinematheque.parse_film_page(
            fixture_text("cinematheque/film_sfmf-series-2.html")
        ).film_format
        is None
    )


def test_www_and_bare_links_become_one_address():
    page = """<ol id="eventCalendar"><li><span class="day"><span class="dom">18</span>
      <span class="mon">October</span><span class="year">2026</span></span>
      <ol class="programs">
        <li class="programScreening"><span class="time pm">7:00</span>
          <a class="programTitle" href="https://www.thecinematheque.ca/films/2026/kwaidan">Kwaidan</a></li>
        <li class="programScreening"><span class="time pm">9:30</span>
          <a class="programTitle" href="https://thecinematheque.ca/films/2026/kwaidan">Kwaidan</a></li>
      </ol></li></ol>"""
    screenings = cinematheque.parse_calendar(page)
    assert {s.info_url for s in screenings} == {"https://thecinematheque.ca/films/2026/kwaidan"}


def test_film_page_names_its_series(fixture_text):
    sfmf = cinematheque.parse_film_page(fixture_text("cinematheque/film_sfmf-series-2.html"))
    assert sfmf.series == "Small File Media Festival 2026"
    dark = cinematheque.parse_film_page(fixture_text("cinematheque/film_daughters-darkness.html"))
    assert dark.series == "Forbidden Rooms: Halloween on Howe"
    assert dark.series_url == "https://thecinematheque.ca/series/forbidden-rooms-halloween"
