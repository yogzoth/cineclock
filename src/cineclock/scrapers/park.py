"""The Park Theatre: one listings site (Igniter ticketing) with everything on it.

Each listing has the poster, the credits line with the year, and the ticket
link, so no film pages need fetching. Listings run across several pages.
"""

import re
from datetime import UTC, datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

from cineclock.dates import hour_24, month_number, resolve
from cineclock.models import Screening
from cineclock.scrapers import barker, first_paragraph, make_client
from cineclock.titles import clean_title, credits_year, split_year

VENUE = "The Park"
VENUES = (VENUE,)
ROOM = "park-main"
LISTINGS_URL = "https://tickets.theparktheatre.ca/"
# The site refuses requests that don't say they accept HTML.
HEADERS = {"Accept": "text/html,application/xhtml+xml"}
MAX_PAGES = 10

# "Sun, Oct 18, 1:15 pm" (weekday optional)
DATE = re.compile(
    r"(?:(?P<weekday>[A-Za-z]{3})[a-z]*,\s*)?(?P<month>[A-Za-z]{3})[a-z]*\.?\s+(?P<day>\d{1,2}),?"
    r"\s+(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*(?P<ampm>[ap]m)",
    re.IGNORECASE,
)


def parse_date(text: str, now: datetime) -> datetime | None:
    """Read a Park date like "Sun, Oct 18, 1:15 pm", which has no year, as Vancouver time."""
    match = DATE.search(text)
    month = month_number(match["month"]) if match else None
    if not month:
        return None
    hour = hour_24(int(match["hour"]), match["ampm"])
    return resolve(month, int(match["day"]), hour, int(match["minute"]), match["weekday"], now)


def listing_start(listing: Tag, now: datetime) -> datetime | None:
    date = listing.select_one("p.event-date")
    start = parse_date(date.get_text(" ", strip=True), now) if date else None
    if start:
        return start
    # Backup: the little calendar tile, "Nov / 08 / 12:00 PM", which has no weekday.
    tile = [p.get_text(strip=True) for p in listing.select(".date-preview p")]
    if len(tile) == 3:
        return parse_date(f"{tile[0]} {tile[1]}, {tile[2]}", now)
    return None


def parse_page(page: str, now: datetime) -> tuple[list[Screening], str | None]:
    """Return the screenings on one listings page and the next page's URL, if any."""
    soup = BeautifulSoup(page, "html.parser")
    screenings = []
    for listing in soup.select("div.single-event"):
        headline = listing.select_one("p.event-headline")
        start = listing_start(listing, now)
        link = listing.select_one("div.event-buttons-wrapper a") or listing.select_one(
            "a.image-link"
        )
        # A bundle like "De Gaulle - Résistance & Liberté" has no date of its own.
        if not headline or not start or not link:
            continue

        title, note = clean_title(headline.get_text(" ", strip=True))
        title, title_year = split_year(title)
        description = listing.select_one("div.event-description")
        image = listing.select_one(".img-wrapper img")
        url = urljoin(LISTINGS_URL, link["href"])
        screenings.append(
            Screening(
                venue=VENUE,
                room=ROOM,
                title=title,
                year=(credits_year(description.get_text(" ")) if description else None)
                or title_year,
                note=note,
                starts_at=start,
                url=url,
                info_url=url,
                poster_url=image.get("src") if image else None,
                synopsis=first_paragraph(description),
            )
        )

    next_link = soup.select_one("a.next_page[href]")
    return screenings, urljoin(LISTINGS_URL, next_link["href"]) if next_link else None


def scrape(client: httpx.Client, now: datetime | None = None) -> list[Screening]:
    now = now or datetime.now(UTC)
    screenings = []
    url = LISTINGS_URL
    for _ in range(MAX_PAGES):
        response = client.get(url, headers=HEADERS)
        response.raise_for_status()
        page_screenings, url = parse_page(response.text, now)
        screenings += page_screenings
        if not url:
            break
    return [s for s in screenings if s.starts_at >= now]


FEED_URL = "https://www.theparktheatre.ca/wp-json/barker/v1/listings"


def scrape_feed(client: httpx.Client, now: datetime | None = None) -> list[Screening]:
    """The same listings from the site's WordPress feed (barker.py)."""
    return barker.scrape(client, FEED_URL, VENUE, ROOM, now, events=True)


if __name__ == "__main__":
    with make_client() as client:
        for s in scrape(client):
            print(s.local_start.strftime("%a %b %d %H:%M"), s.title, s.year or "", s.note or "")
