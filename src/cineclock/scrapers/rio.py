"""The Rio Theatre: its WordPress listings feed (barker.py), details from each film page."""

from datetime import datetime

import httpx

from cineclock.models import Screening
from cineclock.scrapers import barker, make_client

VENUE = "The Rio"
VENUES = (VENUE,)
ROOM = "rio-main"
LISTINGS_URL = "https://riotheatre.ca/wp-json/barker/v1/listings"


def parse_listings(data: list[dict]) -> list[Screening]:
    return barker.parse_listings(data, VENUE, ROOM)


parse_film_page = barker.parse_film_page


def scrape(client: httpx.Client, now: datetime | None = None) -> list[Screening]:
    return barker.scrape(client, LISTINGS_URL, VENUE, ROOM, now)


if __name__ == "__main__":
    with make_client() as client:
        for s in scrape(client):
            print(s.local_start.strftime("%a %b %d %H:%M"), s.title, s.year or "", s.note or "")
