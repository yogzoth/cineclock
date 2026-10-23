"""What's playing, shaped for pages: one FilmPage per film, the days of the
schedule grouped by theatre, and the small labels pages share ("Today, 7:00 pm").
Everything here is plain data built from screenings and film details; the
templates decide how it looks.
"""

import calendar
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta

from cineclock.films import Film, match_key, normalize, slugify
from cineclock.models import VANCOUVER, Screening
from cineclock.tags import backed_up, description_tags, screening_tags, sort_tags
from cineclock.timefmt import clock, day_label, short_day
from cineclock.venues import BY_NAME, Room, Venue, order

RARE = ("One screening",)


def choose_poster(screening: Screening, film: Film | None) -> str | None:
    """The Rio's and the Park's own posters first; elsewhere TMDB's poster beats a still."""
    venue = BY_NAME.get(screening.venue)
    if venue and venue.own_posters and screening.poster_url:
        return screening.poster_url
    if film and film.poster_url:
        return film.poster_url
    return screening.poster_url


def venue_for(name: str) -> Venue:
    # A venue missing from the registry still gets a page rather than crashing the build.
    if name in BY_NAME:
        return BY_NAME[name]
    slug = slugify(name)
    return Venue(name, name, slug, "", "", "", "", rooms=(Room(f"{slug}-main", "Main"),))


def series_from_titles(screenings: list[Screening]) -> list[Screening]:
    """Listings at a theatre that share a "<name>: <part>" title ("Shits n Giggles
    2026: Opening Night", "... : Finals and Awards") belong to a series of that
    name, when the theatre hasn't filed them under one."""
    groups: dict[tuple[str, str], set[str]] = {}
    for s in screenings:
        name, colon, part = s.title.partition(":")
        if colon and name.strip() and part.strip():
            groups.setdefault((s.venue, name.strip()), set()).add(normalize(part))
    named = {key for key, parts in groups.items() if len(parts) >= 2}
    return [
        replace(s, series=s.title.partition(":")[0].strip())
        if not s.series and (s.venue, s.title.partition(":")[0].strip()) in named
        else s
        for s in screenings
    ]


def tags_for(s: Screening) -> list[str]:
    # The series name counts too, so a festival programme gets the Festival tag.
    note = " ".join(filter(None, [s.note, s.series])) or None
    tags = screening_tags(s.title, note, s.film_format)
    found = tags + [t for t in description_tags(s.synopsis) if t not in tags]
    # Checked again here so notes saved before a rule existed are held to it too.
    said = " ".join(filter(None, [s.title, s.note, s.series, s.film_format, s.synopsis]))
    found += [t for t in s.ai_tags if t not in found and backed_up(t, said)]
    if "Q&A" in found and "Guests" in found:
        found.remove("Guests")
    return found


def compact(n: int) -> str:
    """1476132 -> 1.5M, 12345 -> 12K"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000:
        return f"{round(n / 1_000)}K"
    return str(n)


def rarity(screenings: list[Screening]) -> list[str]:
    """Flags a film you could easily miss: a single showing. (Jordan dropped "One day
    only" for several showings on one day; one clear flag reads better.)"""
    return ["One screening"] if len(screenings) == 1 else []


@dataclass
class FilmPage:
    slug: str
    title: str
    year: int | None
    film: Film | None
    screenings: list[Screening] = field(default_factory=list)

    @property
    def poster_url(self) -> str | None:
        return choose_poster(self.screenings[0], self.film) if self.screenings else None

    @property
    def next_showing(self) -> Screening:
        return min(self.screenings, key=lambda s: s.starts_at)

    @property
    def added(self) -> datetime | None:
        seen = [s.first_seen for s in self.screenings if s.first_seen]
        return min(seen) if seen else None

    @property
    def tags(self) -> list[str]:
        found = [t for s in self.screenings for t in tags_for(s)]
        # No year, no score and nothing the listing says: that's almost always a
        # one-off event rather than a film, so it gets the general event tag.
        if not found and not self.year and not self.has_score:
            found = ["Event"]
        return sort_tags(found + rarity(self.screenings))

    @property
    def listed_as(self) -> str | None:
        """What the theatre calls it, when that's more than the film's title.
        "<event>: <film>" gives the event ("Food Stash Foundation 10 Year
        Anniversary Celebration"); otherwise the whole listing title ("Claudio
        Simonetti's Goblin Performs TENEBRE"). A credit alone ("Ken Russell's
        'The Devils'") isn't worth a line."""
        if not self.film or not self.film.title:
            return None
        film_words = set(normalize(self.film.title).split())
        for s in self.screenings:
            words = set(normalize(s.title).split())
            if not film_words <= words or len(words - film_words) < 3:
                continue
            event, colon, rest = s.title.partition(":")
            if colon and film_words <= set(normalize(rest).split()):
                return event.strip()
            return s.title
        return None

    @property
    def screening_note(self) -> str | None:
        """The theatre's own description when the listing is an event around the
        film (a live score, a celebration): TMDB's synopsis only covers the film."""
        if not self.listed_as:
            return None
        own = next((s.synopsis for s in self.screenings if s.synopsis), None)
        return own if own and own != self.synopsis else None

    @property
    def synopsis(self) -> str | None:
        """TMDB's synopsis, or failing that the theatre's own description."""
        if self.film and self.film.overview:
            return self.film.overview
        return next((s.synopsis for s in self.screenings if s.synopsis), None)

    @property
    def has_score(self) -> bool:
        f = self.film
        return bool(
            f
            and (
                f.letterboxd_rating
                or f.imdb_rating
                or f.rt_score is not None
                or f.metacritic is not None
            )
        )

    @property
    def venues(self) -> list[Venue]:
        names = sorted({s.venue for s in self.screenings}, key=lambda n: (order(n), n))
        return [venue_for(n) for n in names]

    @property
    def genres(self) -> list[str]:
        return self.film.genre_list if self.film else []

    @property
    def last_showing(self) -> Screening:
        return max(self.screenings, key=lambda s: s.starts_at)

    @property
    def shared_note(self) -> str | None:
        """The venue's note when every showing has the same one, to show once up top."""
        notes = {s.note for s in self.screenings}
        return notes.pop() if len(notes) == 1 else None

    def span(self, now: datetime) -> str:
        """When it's on, for a card: "Wednesday 21 only", "Tue 20 to Sat 24", or
        plain dates past the coming week ("Oct 30 to Nov 2"). Weekday names
        stay true between builds, unlike Today and Tomorrow, so those are left out."""
        today = now.astimezone(VANCOUVER).date()
        first = self.next_showing.local_start.date()
        last = self.last_showing.local_start.date()
        if first == last:
            return f"{week_day(first, today)} only"
        # Weekdays only when the whole run is this week, so both ends read alike.
        if (last - today).days < 7:
            return f"{first:%a} {first.day} to {last:%a} {last.day}"
        return f"{first:%b} {first.day} to {last:%b} {last.day}"

    @property
    def series(self) -> str | None:
        """The festival or series the theatre lists it under, if any."""
        names = [s.series for s in self.screenings if s.series]
        return Counter(names).most_common(1)[0][0] if names else None

    @property
    def date_range(self) -> str:
        """Oct 18 only, or Oct 18 to Nov 2"""
        first = self.next_showing.local_start.date()
        last = self.last_showing.local_start.date()
        if first == last:
            return f"{first:%b} {first.day} only"
        return f"{first:%b} {first.day} to {last:%b} {last.day}"


@dataclass
class Row:
    """One film at one venue on one day: a line on the showtimes page."""

    page: FilmPage
    title: str
    year: int | None
    note: str | None
    poster_url: str | None
    showtimes: list[Screening]

    @property
    def tags(self) -> list[str]:
        return sort_tags(
            [t for s in self.showtimes for t in tags_for(s)] + rarity(self.page.screenings)
        )


@dataclass
class VenueGroup:
    venue: Venue
    rows: list[Row]


@dataclass
class Day:
    date: date
    label: str
    short: str
    groups: list[VenueGroup]

    @property
    def anchor(self) -> str:
        return f"d{self.date:%Y%m%d}"


@dataclass
class CalendarDay:
    date: date
    anchor: str | None  # set when there are showings that day
    today: bool


@dataclass
class CalendarMonth:
    title: str
    weeks: list[list[CalendarDay | None]]


def calendar_months(days: list[Day], now: datetime) -> list[CalendarMonth]:
    """Month grids covering every listed day, weeks starting on Sunday."""
    if not days:
        return []
    listed = {d.date: d.anchor for d in days}
    today = now.astimezone(VANCOUVER).date()
    first, last = min(listed), max(listed)
    months = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        weeks = [
            [CalendarDay(d, listed.get(d), d == today) if d.month == month else None for d in week]
            for week in calendar.Calendar(firstweekday=6).monthdatescalendar(year, month)
        ]
        months.append(CalendarMonth(f"{date(year, month, 1):%B %Y}", weeks))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


@dataclass
class TheatreRun:
    """Back-to-back days at one theatre, shown as one block on a film page."""

    venue: Venue
    days: list[tuple[Day, list[Row]]]


def theatre_runs(days: list[Day]) -> list[TheatreRun]:
    """A film's showtimes by theatre: each theatre once, in order of its first
    date, with every day it plays there."""
    runs: dict[str, TheatreRun] = {}
    for day in days:
        for group in day.groups:
            run = runs.setdefault(group.venue.slug, TheatreRun(group.venue, []))
            run.days.append((day, group.rows))
    return list(runs.values())


def film_pages(screenings: list[Screening], films: dict[str, Film]) -> dict[int, FilmPage]:
    """Group screenings into one page per film. Returns {id(screening): page}."""
    pages: dict[tuple, FilmPage] = {}
    used_slugs: set[str] = set()
    by_screening = {}
    for s in sorted(screenings, key=lambda s: s.starts_at):
        film = films.get(match_key(s.title, s.year))
        # Matched films share a page across venues, even when venues word the
        # title differently ("Ken Russell's 'The Devils'" and "The Devils").
        if film and film.matched:
            identity = ("tmdb", film.tmdb_id)
            title, year = film.title or s.title, film.year or s.year
        else:
            identity = ("title", match_key(s.title, s.year))
            title, year = s.title, s.year
        if identity not in pages:
            base = slugify(title) + (f"-{year}" if year else "")
            slug, n = base, 2
            while slug in used_slugs:
                slug, n = f"{base}-{n}", n + 1
            used_slugs.add(slug)
            pages[identity] = FilmPage(slug, title, year, film if film and film.matched else None)
        pages[identity].screenings.append(s)
        by_screening[id(s)] = pages[identity]
    return by_screening


def group_days(
    screenings: list[Screening], page_of: dict[int, FilmPage], films: dict[str, Film], now: datetime
) -> list[Day]:
    today = now.astimezone(VANCOUVER).date()
    by_day: dict[date, list[Screening]] = defaultdict(list)
    for s in sorted(screenings, key=lambda s: s.starts_at):
        by_day[s.local_start.date()].append(s)

    days = []
    for day in sorted(by_day):
        by_venue: dict[str, list[Screening]] = defaultdict(list)
        for s in by_day[day]:
            by_venue[s.venue].append(s)
        groups = []
        for name in sorted(by_venue, key=lambda n: (order(n), n)):
            rows: dict[tuple, Row] = {}
            for s in by_venue[name]:
                # Two showings of a film on one day share a row, unless the venue
                # labels them differently (a 70mm show and a regular one).
                key = (id(page_of[id(s)]), s.note)
                if key not in rows:
                    film = films.get(match_key(s.title, s.year))
                    rows[key] = Row(
                        page_of[id(s)], s.title, s.year, s.note, choose_poster(s, film), []
                    )
                rows[key].showtimes.append(s)
            groups.append(VenueGroup(venue_for(name), list(rows.values())))
        days.append(Day(day, day_label(day, today), short_day(day, today), groups))
    return days


def week_day(day: date, today: date, short: bool = False) -> str:
    """Within the coming week, the weekday ("Tuesday 21", or "Tue 21" short);
    after that, the date ("Oct 30")."""
    if 0 <= (day - today).days < 7:
        return f"{day:%a} {day.day}" if short else f"{day:%A} {day.day}"
    return f"{day:%b} {day.day}"


def when(s: Screening, now: datetime) -> str:
    """Today, 7:00 pm / Tomorrow, 7:00 pm / Tuesday 21, 7:00 pm / Oct 30, 7:00 pm"""
    today = now.astimezone(VANCOUVER).date()
    day = s.local_start.date()
    if day == today:
        return f"Today, {clock(s.starts_at)}"
    if day == today + timedelta(days=1):
        return f"Tomorrow, {clock(s.starts_at)}"
    return f"{week_day(day, today)}, {clock(s.starts_at)}"


def last_day(s: Screening, now: datetime) -> str:
    """today, tomorrow, or a short date: "Ends Tue 20" """
    today = now.astimezone(VANCOUVER).date()
    day = s.local_start.date()
    if day == today:
        return "today"
    if day == today + timedelta(days=1):
        return "tomorrow"
    return f"{day:%a} {day.day}"
