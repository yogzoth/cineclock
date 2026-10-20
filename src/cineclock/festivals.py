"""Art and a blurb for each festival or series, from the theatre's own page for it.

    python -m cineclock.festivals

The Cinematheque links every film to its programme's page ("Forbidden Rooms:
Halloween on Howe"), and that page has a banner and a line about it for link
previews. Each page is read once every two weeks, a few seconds apart, so a
deploy adds at most a handful of requests. Series without a page fall back to
a film's backdrop when the site is built.
"""

import sys
import time
from datetime import UTC, datetime, timedelta

import httpx
from bs4 import BeautifulSoup

from cineclock import db
from cineclock.scrapers import make_client, page_summary

FRESH_FOR = timedelta(days=14)
PER_RUN = 12


def parse_series_page(page: str) -> tuple[str | None, str | None]:
    soup = BeautifulSoup(page, "html.parser")
    image = soup.find("meta", property="og:image")
    image = (image.get("content") or "").strip() if image else ""
    return (image if image.startswith("https://") else None), page_summary(soup)


def due(conn, now: datetime) -> list[str]:
    known = db.load_series_pages(conn)
    urls = {s.series_url for s in db.upcoming(conn, now) if s.series_url}
    stale = now - FRESH_FOR
    return sorted(
        u
        for u in urls
        if u.startswith("https://")
        and (u not in known or datetime.fromisoformat(known[u]["checked"]) < stale)
    )


def main() -> int:
    conn = db.connect()
    now = datetime.now(UTC)
    todo = due(conn, now)[:PER_RUN]
    with make_client() as client:
        for i, url in enumerate(todo):
            if i:
                time.sleep(3)
            try:
                response = client.get(url)
                response.raise_for_status()
            except httpx.HTTPError as error:
                print(f"::warning::{url}: {error}")
                continue
            image, blurb = parse_series_page(response.text)
            db.save_series_page(conn, url, image, blurb, now)
            conn.commit()
    print(f"::notice::Read {len(todo)} festival and series pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
