"""Letterboxd: the film's page, its average rating and how many people rated it.

There's no public API, but letterboxd.com/tmdb/<id>/ redirects to the film's
page. The page states the average in its share tags, and its structured data
(the JSON search engines read) has the number of ratings.
"""

import json
import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup


@dataclass(frozen=True)
class LetterboxdFilm:
    url: str | None = None
    rating: float | None = None
    rating_count: int | None = None


def tmdb_url(tmdb_id: int) -> str:
    return f"https://letterboxd.com/tmdb/{tmdb_id}/"


def structured_data(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        # Letterboxd wraps the JSON in /* <![CDATA[ */ ... /* ]]> */
        text = re.sub(r"/\*.*?\*/", "", script.string or "", flags=re.S)
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def parse_film_page(page: str) -> LetterboxdFilm:
    soup = BeautifulSoup(page, "html.parser")
    og_url = soup.find("meta", property="og:url")
    # <meta name="twitter:data2" content="3.85 out of 5">
    data2 = soup.find("meta", attrs={"name": "twitter:data2"})
    match = re.match(r"\s*(\d(?:\.\d+)?) out of 5", data2.get("content", "")) if data2 else None
    count = structured_data(soup).get("aggregateRating", {}).get("ratingCount")
    return LetterboxdFilm(
        url=(og_url.get("content") if og_url else None) or None,
        rating=round(float(match.group(1)), 2) if match else None,
        rating_count=int(count) if isinstance(count, int | float) else None,
    )


def lookup(client: httpx.Client, tmdb_id: int) -> LetterboxdFilm:
    response = client.get(tmdb_url(tmdb_id))
    response.raise_for_status()
    found = parse_film_page(response.text)
    return LetterboxdFilm(found.url or str(response.url), found.rating, found.rating_count)


def tmdb_id_on_page(page: str, year: int) -> int | None:
    """The TMDB id on a Letterboxd film page, if the page is the film from `year`
    (a year either way, as release years differ between sites)."""
    soup = BeautifulSoup(page, "html.parser")
    title = soup.find("meta", property="og:title")
    shown = re.search(r"\((\d{4})\)\s*$", title.get("content", "")) if title else None
    body = soup.find(attrs={"data-tmdb-id": True})
    if not shown or not body or abs(int(shown[1]) - year) > 1:
        return None
    found = body["data-tmdb-id"]
    return int(found) if found.isdigit() else None


def find_tmdb_id(client: httpx.Client, title: str, year: int, slug: str) -> int | None:
    """When TMDB's search can't find a film, Letterboxd's address for it often
    can: /film/begotten/, or /film/begotten-1989/ when the name is shared.
    Letterboxd's page carries the TMDB id, so the rest comes from TMDB as usual."""
    for path in (f"{slug}-{year}", slug):
        response = client.get(f"https://letterboxd.com/film/{path}/")
        if response.status_code != 200:
            continue
        found = tmdb_id_on_page(response.text, year)
        if found:
            return found
    return None
