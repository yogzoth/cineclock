from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from zoneinfo import ZoneInfo

# Load Vancouver's rules from the pinned tzdata package rather than the OS copy.
# B.C. dropped seasonal clock changes in 2026, and an outdated system database
# would quietly show every winter screening an hour early.
with files("tzdata.zoneinfo.America").joinpath("Vancouver").open("rb") as f:
    VANCOUVER = ZoneInfo.from_file(f, key="America/Vancouver")


@dataclass(frozen=True)
class Screening:
    """One showing of one film at one venue."""

    venue: str
    title: str
    starts_at: datetime
    url: str
    # Which room in the venue, by its stable ID ("viff-lochmaddy"). None if unknown.
    room: str | None = None
    info_url: str | None = None
    year: int | None = None
    note: str | None = None
    poster_url: str | None = None
    # As the venue lists it: "35mm", "DCP". Only the Cinematheque says, so far.
    film_format: str | None = None
    # The programme or festival the venue files it under ("Small File Media Festival 2026").
    series: str | None = None
    # The theatre's own page for that programme, where its art comes from.
    series_url: str | None = field(default=None, compare=False)
    # The film's IMDb id, when the theatre's page links it ("tt0080360").
    imdb_id: str | None = field(default=None, compare=False)
    # The theatre's own description, used when TMDB has none.
    synopsis: str | None = None
    # Why there's no booking link, as the theatre puts it ("Sold out", "Standby").
    status: str | None = None
    # When cineclock first saw this listing. Not part of what makes two screenings equal.
    first_seen: datetime | None = field(default=None, compare=False)
    # Tags the weekly AI read found in the listing that the patterns missed (ai.py).
    ai_tags: tuple[str, ...] = field(default=(), compare=False)

    def __post_init__(self):
        # Scrapers must hand us a real moment in time, not a bare clock reading.
        if self.starts_at.tzinfo is None:
            raise ValueError("starts_at must be timezone-aware")
        object.__setattr__(self, "starts_at", self.starts_at.astimezone(UTC))

    @property
    def local_start(self) -> datetime:
        return self.starts_at.astimezone(VANCOUVER)
