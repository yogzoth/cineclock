"""The one Jinja setup, shared by the web app and the static site build."""

import hashlib
import os
import re
import struct
import zlib
from functools import cache
from importlib import metadata
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from cineclock.models import VANCOUVER
from cineclock.structured import film_events
from cineclock.tags import filter_slugs, kind, said_by_tags, slug
from cineclock.timefmt import clock, stamp
from cineclock.venues import room_label

RELAY_URL = "https://cineclock-relay.cineclock-relay.workers.dev"

STATIC = Path(__file__).parent / "static"

env = Environment(loader=PackageLoader("cineclock"), autoescape=select_autoescape())
env.filters["clock"] = clock


def fixed_zone(now) -> str:
    """Vancouver's offset when the page was built, as a fixed zone the browser
    can use ("Etc/GMT+7" is UTC-7). Browsers with time zone data from before
    B.C. kept daylight time all year would otherwise move Vancouver to UTC-8 in
    November, and Today, Tomorrow and On soon would be an hour out."""
    minutes = int(now.astimezone(VANCOUVER).utcoffset().total_seconds() // 60)
    if minutes % 60:
        return "America/Vancouver"
    hours = -minutes // 60
    return f"Etc/GMT{'+' if hours >= 0 else '-'}{abs(hours)}"


env.filters["fixed_zone"] = fixed_zone


def score_value(film, source: str) -> float | None:
    """A film's score from one source, as a number for sorting."""
    if film is None:
        return None
    return {
        "letterboxd": film.letterboxd_rating,
        "imdb": film.imdb_rating,
        "rt": film.rt_score,
        "metacritic": film.metacritic,
    }[source]


def score_text(film, source: str) -> str:
    """How a card shows that score: "3.9", "7.4", "92%", "81"; "" if there's none."""
    value = score_value(film, source)
    if value is None or (source in ("letterboxd", "imdb") and not value):
        return ""
    if source in ("letterboxd", "imdb"):
        return f"{value:.1f}"
    return f"{value}%" if source == "rt" else str(value)


env.filters["score_text"] = score_text


# A steady colour for each title, for posters that haven't been found yet.
def card_crop(venue) -> str:
    """Zoom a theatre card's photo so its sign sits right of the logo (at 72%
    across, 40% down), never leaving a gap at an edge."""
    if not venue.sign_at:
        return f"object-position: {venue.photo_focus}"
    x, y = venue.sign_at
    z = venue.sign_zoom

    def shift(at: float, target: float) -> float:
        return round(max(1 - z, min(0.0, target - z * at)) * 100, 1)

    return (
        f"object-position: {x * 100:.0f}% {y * 100:.0f}%; transform-origin: 0 0; "
        f"transform: translate({shift(x, 0.72)}%, {shift(y, 0.4)}%) scale({z})"
    )


env.filters["card_crop"] = card_crop
env.filters["hue"] = lambda text: zlib.crc32(str(text).encode()) % 360
env.filters["stamp"] = stamp
env.filters["tagslug"] = slug
env.filters["filter_slugs"] = filter_slugs
env.filters["tagkind"] = kind
env.filters["said_by_tags"] = said_by_tags
env.filters["runtime"] = lambda minutes: (
    f"{minutes // 60}h {minutes % 60:02d}m" if minutes >= 60 else f"{minutes}m"
)
env.filters["room_label"] = lambda s: room_label(s.venue, s.room)
# TMDB serves the same poster at several widths; the popup on a film page uses a bigger one.
env.filters["large_poster"] = lambda url: (url or "").replace("/t/p/w500/", "/t/p/w780/")


def asset_version(name: str) -> str:
    """A short fingerprint of a static file, so browsers fetch it again when it changes.

    Phones keep stylesheets cached; without this a new page can arrive with
    the old styles and look broken.
    """
    data = (STATIC / name).read_bytes()
    return hashlib.sha256(data).hexdigest()[:10]


env.globals["asset_version"] = asset_version

try:
    env.globals["version"] = metadata.version("cineclock")
except metadata.PackageNotFoundError:
    env.globals["version"] = "dev"

# The Letterboxd relay (relay/worker.js on Cloudflare). Empty means only synced
# profiles can connect.
env.globals["relay_url"] = os.environ.get("CINECLOCK_RELAY", RELAY_URL)
# Accounts live on the same Worker, at the site's own api subdomain.
env.globals["api_url"] = os.environ.get("CINECLOCK_API", "https://api.cineclock.ca")
env.globals["google_client_id"] = (
    "358607256767-mkorjtl09q7ii10nuhfo1fesjfcvgnnv.apps.googleusercontent.com"
)

# Cloudflare Web Analytics: page views counted without cookies. Not a secret;
# the token is in every page. Empty leaves the beacon out.
env.globals["analytics_token"] = os.environ.get("CINECLOCK_ANALYTICS", "")

# Link previews need full URLs, not the relative ones the pages use.
env.globals["site_url"] = os.environ.get("CINECLOCK_URL", "https://cineclock.ca/")


def has_asset(name: str) -> bool:
    return (STATIC / name).exists()


env.globals["has_asset"] = has_asset
env.globals["image_size"] = lambda name: image_size(name)


@cache
def image_size(name: str) -> tuple[float, float] | None:
    """Pixel size of a PNG (from its header) or an SVG (from its viewBox)."""
    path = STATIC / name
    if not path.exists():
        return None
    if path.suffix == ".svg":
        box = re.search(r'viewBox="[\d.]+ [\d.]+ ([\d.]+) ([\d.]+)"', path.read_text())
        return (float(box[1]), float(box[2])) if box else None
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return float(width), float(height)


def logo_size(name: str, area: float, min_height: float) -> tuple[int, int]:
    """Width and height that give every logo the same area on screen.

    Theatre logos range from nearly square to seven times wider than tall.
    Fixing the height makes the wide ones huge; fixing the width makes the
    square ones huge. Matching the area keeps them looking about the same size.
    """
    width, height = image_size(name) or (3.0, 1.0)
    ratio = width / height
    h = max((area / ratio) ** 0.5, min_height)
    return round(h * ratio), round(h)


env.globals["logo_size"] = logo_size


def full_logo_size(venue, area: float, min_height: float) -> tuple[int, int]:
    """A theatre's full logo at a shared size, adjusted for how heavy it looks."""
    weight = venue.logo_weight
    return logo_size("logos/" + venue.full_logo, area * weight**2, min_height * weight)


env.globals["full_logo_size"] = full_logo_size


def starts(page) -> str:
    """Every showing's local start, for the browser: "2026-10-20T19:00 ..."."""
    return " ".join(sorted(s.local_start.strftime("%Y-%m-%dT%H:%M") for s in page.screenings))


env.filters["starts"] = starts
env.filters["film_events"] = film_events
