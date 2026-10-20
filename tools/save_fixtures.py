"""Save real responses from theatre feeds the workspace can't reach, as test fixtures.

    python tools/save_fixtures.py

Run by the hand-run Fixtures workflow, which commits what it saves. It also
prints, as run notices, how the Park's WordPress feed compares with what the
current Park scraper reads, so the switch can be checked against real data.
"""

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cineclock.scrapers import make_client

OUT = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "park"
FEED = "https://www.theparktheatre.ca/wp-json/barker/v1/listings"


def main() -> int:
    now = datetime.now(UTC)
    params = {
        "status": "publish",
        "per_page": 500,
        "start_date": now.isoformat(),
        "end_date": (now + timedelta(days=60)).isoformat(),
    }
    with make_client() as client:
        response = client.get(FEED, params=params)
        response.raise_for_status()
        listings = response.json()
        (OUT / "barker_listings.json").write_text(json.dumps(listings, indent=1) + "\n")
        print(f"::notice::Park feed: {len(listings)} listings")

        # A couple of film pages, for the year and poster, and an event page if any.
        movies = [i["event"]["link"] for i in listings if "/movie/" in i["event"]["link"]]
        events = [i["event"]["link"] for i in listings if "/movie/" not in i["event"]["link"]]
        for link in list(dict.fromkeys(movies))[:3] + events[:1]:
            slug = link.rstrip("/").rsplit("/", 1)[-1]
            kind = "movie" if "/movie/" in link else "event"
            page = client.get(link)
            page.raise_for_status()
            (OUT / f"barker_{kind}_{slug}.html").write_text(page.text)
            print(f"::notice::saved {kind} page {slug}")

        compare(client, now)
    return 0


def compare(client, now) -> None:
    """The old scraper and the feed, side by side, on today's real listings."""
    from cineclock.scrapers import park

    old_list = park.scrape(client, now=now)
    old = {(s.title.lower(), s.starts_at) for s in old_list}
    new_scrape = getattr(park, "scrape_feed", None)
    if not new_scrape:
        return
    new_list = new_scrape(client, now=now)
    new = {(s.title.lower(), s.starts_at) for s in new_list}
    horizon = min(max((t for _, t in old), default=now), max((t for _, t in new), default=now))
    old = {x for x in old if x[1] <= horizon}
    new = {x for x in new if x[1] <= horizon}
    print(
        f"::notice::Park old {len(old)}, feed {len(new)}, both {len(old & new)}"
        f" (to {horizon:%b %d})"
    )
    for title, when in sorted(old - new, key=lambda x: x[1])[:10]:
        print(f"::warning::only in the old scraper: {title} {when:%b %d %H:%M}")
    for title, when in sorted(new - old, key=lambda x: x[1])[:10]:
        print(f"::warning::only in the feed: {title} {when:%b %d %H:%M}")
    # Years matter most: they're how film details get matched.
    old_years = {s.title.lower(): s.year for s in old_list}
    new_years = {s.title.lower(): s.year for s in new_list}
    for title in sorted(old_years.keys() & new_years.keys()):
        if old_years[title] != new_years[title]:
            print(f"::warning::year for {title}: old {old_years[title]}, feed {new_years[title]}")


if __name__ == "__main__":
    sys.exit(main())
