"""Schema.org data for film pages, so search engines can show showtimes."""

import json

from markupsafe import Markup

from cineclock.listings import FilmPage, venue_for
from cineclock.models import VANCOUVER


def film_events(page: FilmPage, page_url: str) -> Markup:
    film = page.film
    movie = {"@type": "Movie", "name": page.title, "url": page_url}
    if page.year:
        movie["dateCreated"] = str(page.year)
    if page.poster_url and page.poster_url.startswith("http"):
        movie["image"] = page.poster_url
    if film and film.director:
        movie["director"] = [
            {"@type": "Person", "name": n.strip()} for n in film.director.split(",")
        ]
    if film and film.imdb_id:
        movie["sameAs"] = f"https://www.imdb.com/title/{film.imdb_id}/"
    events = []
    for s in sorted(page.screenings, key=lambda s: s.starts_at):
        venue = venue_for(s.venue)
        place = {"@type": "MovieTheater", "name": venue.name}
        if venue.address:
            place["address"] = venue.address
        events.append(
            {
                "@type": "ScreeningEvent",
                "name": page.title,
                "startDate": s.starts_at.astimezone(VANCOUVER).isoformat(),
                "location": place,
                "workPresented": movie,
                "url": s.url,
                "eventAttendanceMode": "https://schema.org/OfflineEventAttendanceMode",
                "eventStatus": "https://schema.org/EventScheduled",
            }
        )
    data = {"@context": "https://schema.org", "@graph": events or [movie]}
    # "</" can't appear inside a script tag.
    text = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return Markup(f'<script type="application/ld+json">{text}</script>')
