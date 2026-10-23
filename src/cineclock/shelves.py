"""The rows of posters: the home page's spotlight and rails, and the rows at the
top of each theatre page. Each picks and orders films for one idea (on today,
leaving soon, on film, just added...) from the FilmPages in listings.py.
"""

from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from markupsafe import Markup

from cineclock.listings import RARE, FilmPage, last_day, when
from cineclock.models import VANCOUVER, Screening
from cineclock.tags import EVENTS, ON_FILM, is_event
from cineclock.tags import slug as tag_slug
from cineclock.timefmt import stamp
from cineclock.venues import BY_NAME, Venue

RAIL_LENGTH = 15
SOON_SPARES = 6
# Below this many Letterboxd ratings, an average says little.
MIN_RATINGS_FOR_TOP = 1000


@dataclass
class Card:
    """A poster in one of the home page's rows. `why` says why it's in that row."""

    page: FilmPage
    next_label: str
    why: str | None = None
    ends: bool = False

    @property
    def stamped(self) -> Markup:
        if self.ends:
            return stamp(self.next_label, self.page.last_showing.starts_at, "ends")
        return stamp(self.next_label, self.page.next_showing.starts_at, "card")


@dataclass
class Rail:
    title: str
    cards: list[Card]
    more: str  # where "See all" goes, relative to the site root
    # Kept current in the browser (static/onsoon.js): a film leaves once its
    # showing starts and comes back in line at its next one.
    live: bool = False


def spotlight(film_list: list[FilmPage], now: datetime) -> FilmPage | None:
    """The film to feature at the top of the home page: the best-rated one still
    to come today with a backdrop image, or failing that the soonest one."""
    today = now.astimezone(VANCOUVER).date()
    tonight = [
        p
        for p in film_list
        if p.film and p.film.backdrop_url and p.next_showing.local_start.date() == today
    ]
    if tonight:
        return max(tonight, key=lambda p: p.film.letterboxd_rating or 0)
    with_art = [p for p in film_list if p.film and p.film.backdrop_url]
    return min(with_art, key=lambda p: p.next_showing.starts_at) if with_art else None


@dataclass
class Slide:
    page: FilmPage
    label: str
    when: str
    at: Screening | None = None
    # A lead slide stands until its last showing that day starts.
    until: datetime | None = None
    built: datetime | None = None

    @property
    def showing(self) -> Screening:
        return self.at or self.page.next_showing

    @property
    def venue(self) -> Venue | None:
        """The theatre of the showing this slide is about: one logo, not one per theatre."""
        return BY_NAME.get(self.showing.venue)

    @property
    def says_once(self) -> bool:
        """The eyebrow already says it's a single screening."""
        return self.label == "One screening"

    @property
    def stamped(self) -> Markup:
        return stamp(self.when, self.showing.starts_at, "card")

    @property
    def run(self) -> str:
        """How long it's around: "One showing only", "Oct 20 to Nov 2"."""
        days = sorted({s.local_start.date() for s in self.page.screenings})
        if len(self.page.screenings) == 1:
            return "One showing only"
        if len(days) == 1:
            return f"{len(self.page.screenings)} showings, one day only"
        return f"Playing {self.page.span(self.built or self.showing.starts_at)}"


def standout(p: FilmPage) -> str | None:
    tags = p.tags
    return next(
        (t for t in tags if t in EVENTS),
        next((t for t in tags if t in ON_FILM), next((t for t in tags if t in RARE), None)),
    )


def leads(
    film_list: list[FilmPage], now: datetime, days: int = 3, per_day: int = 12
) -> list[Slide]:
    """Candidates for the hero's first slide: the films with art playing each of
    the next few days, best rated first. The page picks one at random from the
    earliest day that still has a showing to come, so tonight's films take turns
    until the last one starts, then tomorrow's do."""
    today = now.astimezone(VANCOUVER).date()
    out = []
    for offset in range(days):
        day = today + timedelta(days=offset)
        label = "Tonight" if offset == 0 else "Tomorrow" if offset == 1 else f"{day:%A}"
        playing = []
        for p in film_list:
            shows = [s for s in p.screenings if s.local_start.date() == day and s.starts_at > now]
            if shows and p.film and p.film.backdrop_url:
                playing.append((p, shows))
        playing.sort(key=lambda v: -(v[0].film.letterboxd_rating or 0))
        for page, shows in playing[:per_day]:
            first = min(shows, key=lambda s: s.starts_at)
            until = max(s.starts_at for s in shows)
            out.append(Slide(page, label, when(first, now), first, until, now))
    return out


def highlights(film_list: list[FilmPage], now: datetime, count: int = 3) -> list[Slide]:
    """The rest of the hero: films with art coming up in the next three weeks,
    spread across the theatres so one busy venue doesn't fill it, standouts
    (events, film prints, one-offs) and the best rated first. The page shows a
    random few on each visit."""
    today = now.astimezone(VANCOUVER).date()
    soon = today + timedelta(days=21)
    rest = sorted(
        (
            p
            for p in film_list
            if p.film and p.film.backdrop_url and p.next_showing.local_start.date() <= soon
        ),
        key=lambda p: (
            standout(p) is None,
            -(p.film.letterboxd_rating or 0),
            p.next_showing.starts_at,
        ),
    )
    slides: list[Slide] = []
    shown: Counter = Counter()
    while rest and len(slides) < count:
        # The best pick from whichever theatre has had the fewest slides so far.
        p = min(rest, key=lambda p: min((shown[v.slug] for v in p.venues), default=0))
        rest.remove(p)
        shown.update(v.slug for v in p.venues)
        slides.append(Slide(p, standout(p) or "Coming up", when(p.next_showing, now), built=now))
    return slides


# A scrape takes a few minutes, so anything first seen this close to a theatre's debut is baseline.
DEBUT_GRACE = timedelta(hours=1)


def rails(
    film_list: list[FilmPage],
    now: datetime,
    debuts: dict[str, datetime] | None = None,
    once: bool = True,
) -> list[Rail]:
    today = now.astimezone(VANCOUVER).date()
    soonest = lambda p: p.next_showing.starts_at  # noqa: E731

    # Films that appeared in the last two weeks, newest first. A theatre's first
    # scrape is its baseline, not news, so a newly added theatre doesn't fill the rail.
    if debuts is None:
        debuts = {}
        for p in film_list:
            for s in p.screenings:
                if s.first_seen and (s.venue not in debuts or s.first_seen < debuts[s.venue]):
                    debuts[s.venue] = s.first_seen

    def is_new(p: FilmPage) -> bool:
        seen = [s for s in p.screenings if s.first_seen]
        if not seen:
            return False
        first = min(seen, key=lambda s: s.first_seen)
        debut = debuts.get(first.venue, first.first_seen)
        return first.first_seen > max(debut + DEBUT_GRACE, now - timedelta(days=14))

    just_added = sorted(
        (p for p in film_list if is_new(p)),
        key=lambda p: (-p.added.timestamp(), soonest(p)),
    )

    # A run that ends this week. One-off screenings already carry their own pill.
    last = lambda p: p.last_showing.starts_at  # noqa: E731
    leaving = sorted(
        (
            p
            for p in film_list
            if p.last_showing.local_start.date() <= today + timedelta(days=6)
            and len({s.local_start.date() for s in p.screenings}) > 1
        ),
        key=last,
    )
    special = sorted(
        (p for p in film_list if ON_FILM & set(p.tags) or is_event(p.tags)), key=soonest
    )
    top = sorted(
        (
            p
            for p in film_list
            if p.film
            and p.film.letterboxd_rating
            and (p.film.letterboxd_count or 0) >= MIN_RATINGS_FOR_TOP
        ),
        key=lambda p: -p.film.letterboxd_rating,
    )

    # A film shows once on the home page: each row skips films an earlier row
    # already showed, unless that would leave it too short to be worth a row.
    shown: set[int] = set()

    def fresh(pages):
        if not once:
            return pages[:RAIL_LENGTH]
        unseen = [p for p in pages if id(p) not in shown]
        if len(unseen) < MIN_HOME_RAIL:
            unseen += [p for p in pages if id(p) in shown]
        picked = unseen[:RAIL_LENGTH]
        shown.update(id(p) for p in picked)
        return picked

    def soon_cards(pages):
        picked = pages[: RAIL_LENGTH + SOON_SPARES]
        shown.update(id(p) for p in picked[:RAIL_LENGTH])
        return [Card(p, when(p.next_showing, now)) for p in picked]

    # `why` is kept on each card for reference but not drawn; the rail's title says it.
    # Leaving soon swaps the date line for when the run ends.
    def cards(pages, why=None, ends=False):
        return [
            Card(
                p,
                f"Ends {last_day(p.last_showing, now)}" if ends else when(p.next_showing, now),
                why(p) if why else None,
                ends,
            )
            for p in fresh(pages)
        ]

    # The film's own year, from TMDB when matched, else what the theatre says.
    def year(p):
        return (p.film.year if p.film else None) or p.year

    classics = sorted(
        (p for p in film_list if (year(p) or today.year) <= today.year - 30), key=soonest
    )
    new = sorted((p for p in film_list if (year(p) or 0) >= today.year - 1), key=soonest)
    genre, in_genre = genre_of_the_day(film_list, today)

    genre_title = GENRE_TITLES.get(genre, genre)
    if genre == "Horror" and today.month == 10:
        genre_title = "Spooky season"

    # Timely rows first (what's on, what's going, what's new), then the
    # picks, then rows by era, closing on the best rated.
    found = [
        # What's playing next, in order. A few spares wait at the end for when
        # the browser drops films whose showing has started.
        Rail("On soon", soon_cards(sorted(film_list, key=soonest)), "showtimes/", live=True),
        Rail(
            "Leaving soon",
            cards(leaving, lambda p: f"Ends {last_day(p.last_showing, now)}", ends=True),
            "films/?sort=leaving",
        ),
        Rail("Just added", cards(just_added), "films/?sort=added"),
        Rail(
            "Special screenings",
            cards(special, lambda p: next(t for t in p.tags if t in ON_FILM or t in EVENTS)),
            "films/?tag=special",
        ),
    ]
    if genre:
        found.append(Rail(genre_title, cards(in_genre), f"films/?genre={tag_slug(genre)}"))
    if len(new) >= MIN_HOME_RAIL:
        found.append(Rail("New releases", cards(new), "films/"))
    if len(classics) >= MIN_HOME_RAIL:
        found.append(Rail("Classics on the big screen", cards(classics), "films/"))
    # The card already shows the Letterboxd score.
    found.append(Rail("Highly rated", cards(top), "films/?sort=rated"))
    return [r for r in found if r.cards]


# Genres too broad to say much as a row of their own.
BROAD_GENRES = {"Drama", "Comedy", "Thriller", "Romance", "Action", "Adventure", "Crime"}
GENRE_TITLES = {
    "Horror": "Horror",
    "Documentary": "Documentaries",
    "Animation": "Animated",
    "Science Fiction": "Sci-fi",
    "Music": "Music on screen",
}


def genre_of_the_day(film_list: list[FilmPage], today) -> tuple[str | None, list[FilmPage]]:
    """One genre row that changes each day, from genres with enough films for a
    row. Horror gets October to itself."""
    by_genre: dict[str, list[FilmPage]] = {}
    for p in film_list:
        for g in p.genres:
            if g not in BROAD_GENRES:
                by_genre.setdefault(g, []).append(p)
    choices = sorted(g for g, pages in by_genre.items() if len(pages) >= MIN_HOME_RAIL)
    if not choices:
        return None, []
    genre = (
        "Horror"
        if today.month == 10 and "Horror" in choices
        else choices[today.toordinal() % len(choices)]
    )
    return genre, sorted(by_genre[genre], key=lambda p: p.next_showing.starts_at)


# A home row with fewer films than this that it hasn't shown yet tops up with
# repeats, so Leaving soon isn't left with two or three posters just because On
# soon already showed the rest.
MIN_HOME_RAIL = 6

# A topic rail on a theatre page needs at least this many films to be worth a row.
MIN_THEATRE_RAIL = 3


def theatre_rails(
    venue: Venue, film_list: list[FilmPage], now: datetime, debuts: dict[str, datetime]
) -> list[Rail]:
    """Poster rows for a theatre page. "Now playing" always has every film here, so
    even a two-film theatre gets a full row. Topic rows (Leaving soon, Events and
    so on) only show when they have enough films and say something new."""
    here = [
        replace(p, screenings=[s for s in p.screenings if s.venue == venue.name])
        for p in film_list
        if any(s.venue == venue.name for s in p.screenings)
    ]
    if not here:
        return []
    by_title = {r.title: r for r in rails(here, now, debuts, once=False)}
    everything = sorted(here, key=lambda p: p.next_showing.starts_at)
    playing = Rail(
        "Now playing",
        [Card(p, when(p.next_showing, now)) for p in everything],
        f"films/?venue={venue.slug}",
    )
    found = [playing]
    for title, sort in [
        ("Leaving soon", "leaving"),
        ("Special screenings", "next&tag=special"),
        ("Just added", "added"),
    ]:
        r = by_title.get(title)
        if r and MIN_THEATRE_RAIL <= len(r.cards) < len(here) and len(found) < 3:
            found.append(replace(r, more=f"films/?venue={venue.slug}&sort={sort}"))
    return found
