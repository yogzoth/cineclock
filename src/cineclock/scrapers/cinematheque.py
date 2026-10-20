"""The Cinematheque: a month-by-month calendar page, details from each film page.

The calendar is plain HTML: one entry per day with the full date, then each
screening's time, ticket link and film page.
"""

import re
from datetime import UTC, datetime

import httpx
from bs4 import BeautifulSoup

from cineclock.models import VANCOUVER, Screening
from cineclock.scrapers import FilmDetails, add_details, make_client, page_summary
from cineclock.titles import clean_title, split_year

VENUE = "The Cinematheque"
VENUES = (VENUE,)
ROOM = "cinematheque-main"
CALENDAR_URL = "https://thecinematheque.ca/films/calendar"
HEADERS = {"Accept": "text/html,application/xhtml+xml"}


def tidy_url(url: str) -> str:
    # The calendar mixes www.thecinematheque.ca and thecinematheque.ca links to
    # the same pages; one address per film means one fetch per film.
    return url.replace("://www.thecinematheque.ca", "://thecinematheque.ca")


MONTHS = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]  # fmt: skip


def parse_start(day: str, month: str, year: str, clock: str, ampm: str) -> datetime | None:
    """Turn the calendar's pieces, like 18 October 2026 at 8:40 pm, into Vancouver time."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", clock)
    if not match or month not in MONTHS or ampm not in ("am", "pm"):
        return None
    hour = int(match.group(1)) % 12 + (12 if ampm == "pm" else 0)
    return datetime(
        int(year), MONTHS.index(month) + 1, int(day), hour, int(match.group(2)), tzinfo=VANCOUVER
    )


def parse_calendar(page: str) -> list[Screening]:
    soup = BeautifulSoup(page, "html.parser")
    calendar = soup.select_one("#eventCalendar")
    if not calendar:
        return []

    screenings = []
    for day in calendar.find_all("li", recursive=False):
        parts = {name: day.select_one(f"span.day .{name}") for name in ("dom", "mon", "year")}
        if not all(parts.values()):
            continue
        dom, mon, year = (parts[name].get_text(strip=True) for name in ("dom", "mon", "year"))

        # Rentals, the AGM and closed days use other classes; only these are films.
        for entry in day.select("ol.programs > li.programScreening"):
            time = entry.select_one(".time")
            film = entry.select_one("a.programTitle")
            tickets = entry.select_one("a.cta")
            if not time or not film:
                continue
            ampm = next((c for c in time.get("class", []) if c in ("am", "pm")), "")
            start = parse_start(dom, mon, year, time.get_text(strip=True), ampm)
            if not start:
                continue
            title, note = clean_title(film.get_text(" ", strip=True))
            title, title_year = split_year(title)
            screenings.append(
                Screening(
                    venue=VENUE,
                    room=ROOM,
                    title=title,
                    year=title_year,
                    note=note,
                    starts_at=start,
                    url=tickets["href"] if tickets else tidy_url(film["href"]),
                    info_url=tidy_url(film["href"]),
                )
            )
    return screenings


def parse_film_page(page: str) -> FilmDetails:
    soup = BeautifulSoup(page, "html.parser")
    # The first details list is the headline film; later ones are for double
    # bills and related films. The "2026" in the page URL is the program year,
    # not the film's.
    year_tag = soup.select_one("ul#details .filmYear")
    year_text = year_tag.get_text(strip=True) if year_tag else ""
    og_image = soup.find("meta", property="og:image")
    film_format = soup.select_one("ul#details .filmFormat")
    # The breadcrumb names the programme in full ("Small File Media Festival 2026");
    # the details list only has a short form ("SFMF 2026").
    series = soup.select_one('#breadcrumbs a[href*="/series/"]')
    return FilmDetails(
        year=int(year_text) if re.fullmatch(r"(18|19|20)\d{2}", year_text) else None,
        poster_url=(og_image.get("content") if og_image else None) or None,
        film_format=(film_format.get_text(strip=True) or None) if film_format else None,
        series=(series.get_text(" ", strip=True) or None) if series else None,
        series_url=series.get("href") if series else None,
        synopsis=page_summary(soup),
    )


def scrape(client: httpx.Client, now: datetime | None = None) -> list[Screening]:
    now = now or datetime.now(UTC)
    response = client.get(CALENDAR_URL, headers=HEADERS)
    response.raise_for_status()
    screenings = [s for s in parse_calendar(response.text) if s.starts_at >= now]
    return add_details(client, screenings, parse_film_page)


if __name__ == "__main__":
    with make_client() as client:
        for s in scrape(client):
            print(s.local_start.strftime("%a %b %d %H:%M"), s.title, s.year or "", s.note or "")
