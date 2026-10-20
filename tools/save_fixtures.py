"""Save real responses from theatre feeds the workspace can't reach, as test fixtures.

    python tools/save_fixtures.py

Run by the hand-run Fixtures workflow, which commits what it saves. Rerun it
when the Park's feed or film pages change shape, then update tests/test_park.py.
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

    return 0


if __name__ == "__main__":
    sys.exit(main())
