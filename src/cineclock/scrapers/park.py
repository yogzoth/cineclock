"""The Park Theatre: its WordPress listings feed (barker.py), details from each film page.

The site runs the same listings plugin as the Rio. Until October 2026 this read
the ticketing site's pages instead; the feed gives exact times (with their UTC
offset, so no guessing the year), the showtime rather than when doors open, and
listings the pages left out.
"""

from datetime import datetime

import httpx

from cineclock.models import Screening
from cineclock.scrapers import barker, make_client

VENUE = "The Park"
VENUES = (VENUE,)
ROOM = "park-main"
LISTINGS_URL = "https://www.theparktheatre.ca/wp-json/barker/v1/listings"


def parse_listings(data: list[dict]) -> list[Screening]:
    # The Park's /event/ pages that sell tickets are screenings too.
    return barker.parse_listings(data, VENUE, ROOM, events=True)


parse_film_page = barker.parse_film_page


def scrape(client: httpx.Client, now: datetime | None = None) -> list[Screening]:
    return barker.scrape(client, LISTINGS_URL, VENUE, ROOM, now, events=True)


if __name__ == "__main__":
    with make_client() as client:
        for s in scrape(client):
            print(s.local_start.strftime("%a %b %d %H:%M"), s.title, s.year or "", s.note or "")
