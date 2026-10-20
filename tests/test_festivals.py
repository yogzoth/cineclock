from datetime import UTC, datetime, timedelta

from cineclock import db
from cineclock.festivals import due, parse_series_page
from cineclock.models import Screening

NOW = datetime(2026, 10, 20, 8, 0, tzinfo=UTC)
URL = "https://thecinematheque.ca/series/forbidden-rooms-halloween"

PAGE = """<html><head>
<meta property="og:image" content="https://thecinematheque.ca/media/forbidden-rooms.jpg">
<meta property="og:description" content="Every October the Cinematheque opens its
vaults for a month of the strangest, scariest horror films ever put to celluloid.">
</head><body></body></html>"""


def test_a_series_page_gives_its_banner_and_blurb():
    image, blurb = parse_series_page(PAGE)
    assert image == "https://thecinematheque.ca/media/forbidden-rooms.jpg"
    assert blurb.startswith("Every October the Cinematheque opens its vaults")


def test_a_page_without_a_secure_image_gives_none():
    assert parse_series_page('<meta property="og:image" content="http://x/a.jpg">') == (None, None)


def test_each_series_page_is_read_again_only_after_two_weeks():
    conn = db.connect(":memory:")
    show = Screening(
        "The Cinematheque", "Kwaidan", NOW + timedelta(days=30), "https://x/1",
        series="Forbidden Rooms: Halloween on Howe", series_url=URL,
    )  # fmt: skip
    db.sync_venue(conn, "The Cinematheque", [show], NOW)
    assert db.upcoming(conn, NOW)[0].series_url == URL
    assert due(conn, NOW) == [URL]
    db.save_series_page(conn, URL, "https://x/a.jpg", None, NOW)
    assert due(conn, NOW + timedelta(days=13)) == []
    assert due(conn, NOW + timedelta(days=15)) == [URL]
