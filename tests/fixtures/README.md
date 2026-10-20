# Fixtures

Saved venue responses, so tests never touch the network.

- `rio/listings.json`: real response from the Rio's listings API, from MovieClock's test suite (MIT, Jordan Hudson).
- `rio/listings_2026-10.json`: a few items from the live listings API, Oct 18 2026.
- `rio/movie_musk.html`, `rio/movie_scream.html`: full film pages saved Oct 18 2026. Scream's credits line has no comma before the year.
- `viff/calendar.html`: VIFF's calendar page, saved Oct 18 2026, for reading the API token.
- `viff/instances_2026-10.json`: a few items from VIFF's calendar API, Oct 18 2026, including a live show.
- `viff/film_*.html`: full VIFF film pages, Oct 18 2026.
- `park/barker_listings.json`: the Park's WordPress listings feed, Oct 20 2026 (saved by the Fixtures workflow). Includes ticketed /event/ screenings and a private booking with no tickets.
- `park/barker_movie_*.html`, `park/barker_event_*.html`: Park film and event pages. Jim Queen's has no year, only an IMDb link; The Devils' summary opens with a review quote.
- `cinematheque/calendar.html`: the Cinematheque's calendar, Oct 18 2026, with rentals, an AGM notice and closed days mixed in.
- `cinematheque/film_*.html`: a feature and a shorts program with no year, Oct 18 2026.
- `enrich/tmdb_*.json`: real TMDB responses for The Thing, Oct 18 2026 (credits trimmed to a few people).
- `enrich/letterboxd_the_thing.html`: the <head> and structured-data script of Letterboxd's real page for The Thing, Oct 18 2026.
- `enrich/omdb_tt0084787.json`: real OMDb response for The Thing, Oct 18 2026.
