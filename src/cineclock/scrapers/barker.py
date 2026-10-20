"""Theatres whose sites run the Barker listings plugin for WordPress (the Rio and
the Park). Both publish the same JSON feed at /wp-json/barker/v1/listings:

    [{"start_time": "2026-10-18T13:15:00-07:00",
      "tickets_link": "https://...",
      "event": {"title": "Tony", "link": "https://.../movie/tony/"}}, ...]

Times carry their UTC offset, so they're exact. The year, poster and
description come from each film's page.
"""

from datetime import UTC, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from cineclock.models import Screening
from cineclock.scrapers import FilmDetails, add_details, page_summary
from cineclock.titles import clean_title, credits_year, split_year

DAYS_AHEAD = 60


def parse_listings(
    data: list[dict], venue: str, room: str, events: bool = False
) -> list[Screening]:
    """Films from /movie/ pages. With `events`, also /event/ pages that sell
    tickets: at the Park those are screenings too (a cartoon morning, a film
    fest), where at the Rio they're live shows. A private booking has no
    tickets, so it's never listed."""
    screenings = []
    for item in data:
        event = item["event"]
        if "/movie/" not in event["link"] and not (events and item.get("tickets_link")):
            continue
        title, note = clean_title(event["title"])
        title, year = split_year(title)
        screenings.append(
            Screening(
                venue=venue,
                room=room,
                title=title,
                year=year,
                note=note,
                starts_at=datetime.fromisoformat(item["start_time"]),
                url=item["tickets_link"] or event["link"],
                info_url=event["link"],
            )
        )
    return screenings


def parse_film_page(page: str) -> FilmDetails:
    soup = BeautifulSoup(page, "html.parser")
    og_image = soup.find("meta", property="og:image")
    poster = og_image.get("content") if og_image else None
    return FilmDetails(
        year=credits_year(soup.get_text(" ")),
        poster_url=poster or None,
        synopsis=page_summary(soup),
    )


def scrape(
    client: httpx.Client,
    url: str,
    venue: str,
    room: str,
    now: datetime | None = None,
    events: bool = False,
) -> list[Screening]:
    now = now or datetime.now(UTC)
    params = {
        "status": "publish",
        "per_page": 500,
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=DAYS_AHEAD)).isoformat(),
    }
    response = client.get(url, params=params)
    response.raise_for_status()
    listings = parse_listings(response.json(), venue, room, events)
    return add_details(client, listings, parse_film_page)
