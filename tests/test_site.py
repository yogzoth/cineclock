import re
from datetime import UTC, date, datetime, timedelta

import pytest

from cineclock import db
from cineclock.films import Film, match_key
from cineclock.listings import choose_poster, compact, film_pages, group_days
from cineclock.models import VANCOUVER, Screening
from cineclock.shelves import rails
from cineclock.site import pages, stats
from cineclock.timefmt import clock, day_label, short_day
from cineclock.venues import VENUES, shown_specs

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)  # 1am Oct 18 in Vancouver


def at(day, hour, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=VANCOUVER)


def show(title, starts, venue="The Rio", year=None, note=None, poster=None, info=None, room=None):
    return Screening(
        venue, title, starts, f"https://tix/{venue}/{title}/{starts:%d%H%M}",
        info_url=info, year=year, note=note, poster_url=poster, room=room,
    )  # fmt: skip


def plain(html):
    """Page HTML without inline icons, so tests can match the text around them."""
    return re.sub(r"<svg.*?</svg>", "", html, flags=re.S)


def film(title, year, tmdb_id=None, **extra):
    return Film(match_key(title, year), NOW, tmdb_id=tmdb_id, title=title, year=year, **extra)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def save(conn, *screenings):
    for venue in {s.venue for s in screenings}:
        db.sync_venue(conn, venue, [s for s in screenings if s.venue == venue], NOW)


# Time formatting


@pytest.mark.parametrize(
    "hour, minute, expected",
    [(0, 0, "12:00 am"), (9, 5, "9:05 am"), (12, 0, "12:00 pm"), (23, 45, "11:45 pm")],
)
def test_clock(hour, minute, expected):
    assert clock(at(18, hour, minute)) == expected


def test_day_labels():
    today = date(2026, 10, 18)
    assert day_label(today, today) == "Today, Oct 18"
    assert day_label(date(2026, 10, 19), today) == "Tomorrow, Oct 19"
    assert day_label(date(2026, 11, 6), today) == "Friday, Nov 6"
    assert short_day(date(2026, 10, 20), today) == "Tue 20"


# Posters


def test_rio_and_park_keep_their_own_posters():
    tmdb = film("Tony", 2026, 1, poster_url="https://tmdb/tony.jpg")
    assert choose_poster(
        show("Tony", at(18, 13), "The Park", poster="https://park/tony.jpg"), tmdb
    ) == ("https://park/tony.jpg")


def test_other_venues_use_tmdb_posters_over_stills():
    tmdb = film("Pulse", 2001, 2, poster_url="https://tmdb/pulse.jpg")
    still = show("Pulse", at(18, 20), "The Cinematheque", poster="https://cin/still.jpg")
    assert choose_poster(still, tmdb) == "https://tmdb/pulse.jpg"
    assert choose_poster(still, None) == "https://cin/still.jpg"


def test_rio_falls_back_to_tmdb_without_its_own_poster():
    tmdb = film("Musk", 2026, 3, poster_url="https://tmdb/musk.jpg")
    assert choose_poster(show("Musk", at(18, 12)), tmdb) == "https://tmdb/musk.jpg"


# Film pages


def test_matched_film_shares_one_page_across_venues():
    devils = film("The Devils", 1971, 31767)
    films = {
        match_key("Ken Russell's 'The Devils'", 1971): devils,
        match_key("The Devils", 1971): devils,
    }
    a = show("Ken Russell's 'The Devils'", at(18, 16), "The Park", year=1971)
    b = show("The Devils", at(20, 19), "VIFF Centre", year=1971)
    page_of = film_pages([a, b], films)
    assert page_of[id(a)] is page_of[id(b)]
    assert page_of[id(a)].slug == "the-devils-1971"
    assert page_of[id(a)].title == "The Devils"


def test_unmatched_titles_get_their_own_pages():
    a, b = show("SFMF Series 2", at(18, 13)), show("SFMF Series 3", at(18, 16))
    page_of = film_pages([a, b], {})
    assert page_of[id(a)].slug == "sfmf-series-2"
    assert page_of[id(b)].slug == "sfmf-series-3"


def test_same_title_different_films_get_different_slugs():
    films = {
        match_key("Pulse", 2001): film("Pulse", 2001, 1),
        match_key("Pulse", 2006): film("Pulse", 2006, 2),
        match_key("Pulse", None): film("Pulse", None, 3),
    }
    shows = [show("Pulse", at(18, 20), year=2001), show("Pulse", at(19, 20), year=2006)]
    shows.append(show("Pulse", at(20, 20)))
    slugs = [film_pages(shows, films)[id(s)].slug for s in shows]
    assert slugs == ["pulse-2001", "pulse-2006", "pulse"]


# Grouping by day


def test_late_show_stays_on_its_local_day():
    s = show("Rocky Horror", at(23, 23, 45))
    [day] = group_days([s], film_pages([s], {}), {}, NOW)
    assert day.date == date(2026, 10, 23)


def test_venues_follow_the_set_order():
    shows = [
        show("Mirror", at(18, 19), "The Dunbar"),
        show("Mirror", at(18, 18), "VIFF Centre"),
        show("Pulse", at(18, 20), "The Cinematheque"),
        show("Tony", at(18, 13), "The Park"),
        show("Musk", at(18, 12), "The Rio"),
    ]
    [day] = group_days(shows, film_pages(shows, {}), {}, NOW)
    assert [g.venue.name for g in day.groups] == [
        "The Rio", "The Park", "The Cinematheque", "VIFF Centre", "The Dunbar",
    ]  # fmt: skip


def test_same_film_twice_a_day_shares_a_row_unless_labelled_differently():
    shows = [
        show("Dune: Part Three", at(18, 15), "The Park", note="Presented in 70mm"),
        show("Dune: Part Three", at(18, 18), "The Park", note="Presented in 70mm"),
        show("Dune: Part Three", at(18, 21), "The Park", note="Advance Screening"),
    ]
    [day] = group_days(shows, film_pages(shows, {}), {}, NOW)
    rows = day.groups[0].rows
    assert [(r.note, len(r.showtimes)) for r in rows] == [
        ("Presented in 70mm", 2),
        ("Advance Screening", 1),
    ]


# Whole site


def test_pages_cover_home_films_and_every_venue(conn):
    save(conn, show("Musk", at(18, 12)), show("Pulse", at(18, 20), "The Cinematheque", year=2001))
    built = pages(conn, NOW)
    assert "index.html" in built
    assert "showtimes/index.html" in built
    assert "films/index.html" in built
    assert "theatres/index.html" in built
    assert "films/musk/index.html" in built
    assert "films/pulse-2001/index.html" in built
    for slug in ("rio", "park", "cinematheque", "viff-centre", "dunbar"):
        assert f"theatres/{slug}/index.html" in built


def test_links_are_relative_to_each_page(conn):
    save(conn, show("Musk", at(18, 12)))
    built = pages(conn, NOW)
    assert 'href="films/musk/"' in built["index.html"]
    assert 'href="static/site.css?v=' in built["index.html"]
    assert 'href="../../static/site.css?v=' in built["films/musk/index.html"]
    assert 'href="../../theatres/rio/"' in built["films/musk/index.html"]


def test_film_page_shows_details_ratings_and_links(conn):
    s = show("The Thing", at(31, 17, 40), "VIFF Centre", year=1982, info="https://viff/thing")
    save(conn, s)
    db.save_film(
        conn,
        film(
            "The Thing", 1982, 1091,
            imdb_id="tt0084787", director="John Carpenter", runtime=109,
            overview="A research team in Antarctica.", poster_url="https://tmdb/p.jpg",
            backdrop_url="https://tmdb/b.jpg", letterboxd_url="https://letterboxd.com/film/the-thing/",
            letterboxd_rating=4.35, imdb_rating=8.2, rt_score=85,
        ),
    )  # fmt: skip
    page = pages(conn, NOW)["films/the-thing-1982/index.html"]
    for expected in [
        "Directed by </span><b>John Carpenter</b>",
        "1h 49m",
        "A research team in Antarctica.",
        ">4.3<",
        ">8.2<",
        ">85%<",
        'href="https://letterboxd.com/film/the-thing/"',
        'href="https://www.imdb.com/title/tt0084787/"',
        "rottentomatoes.com/search?search=The+Thing",
        'href="https://tix/VIFF Centre/The Thing/311740"',
        'src="https://tmdb/b.jpg"',
        "has-backdrop",
    ]:
        assert expected in page, expected


def test_film_page_links_go_through_score_tiles(conn):
    save(conn, show("The Thing", at(31, 17, 40), "VIFF Centre", year=1982, info="https://viff/x"))
    db.save_film(conn, film("The Thing", 1982, 1091, imdb_id="tt0084787", letterboxd_rating=4.35))
    page = pages(conn, NOW)["films/the-thing-1982/index.html"]
    assert 'href="https://letterboxd.com/tmdb/1091/"' in page
    assert 'class="links"' not in page
    # The theatre's own page for the film sits under its showtimes, opening in a new tab.
    assert '<p class="run-more"><a href="https://viff/x" target="_blank" rel="noopener">' in page
    assert "More about this screening at VIFF" in page
    assert 'href="../../theatres/viff-centre/">' in page


def test_film_page_hides_ratings_it_does_not_have(conn):
    save(conn, show("The Thing", at(31, 17, 40), year=1982))
    db.save_film(conn, film("The Thing", 1982, 1091, imdb_id="tt0084787", imdb_rating=8.2))
    page = pages(conn, NOW)["films/the-thing-1982/index.html"]
    assert "rating-imdb" in page
    for missing in ("rating-letterboxd", "rating-rt", "rating-metacritic", "–<"):
        assert missing not in page.split("<main>")[1].split("</main>")[0], missing


def test_film_page_without_any_ratings_has_no_score_row(conn):
    save(conn, show("The Thing", at(31, 17, 40), year=1982))
    db.save_film(conn, film("The Thing", 1982, 1091))
    assert 'class="scores"' not in pages(conn, NOW)["films/the-thing-1982/index.html"]


def test_unmatched_film_page_still_has_showtimes(conn):
    save(conn, show("SFMF Series 2", at(18, 13), "The Cinematheque"))
    page = pages(conn, NOW)["films/sfmf-series-2/index.html"]
    assert "1:00 pm" in page
    assert "Letterboxd</a>" not in page.split("<main>")[1].split("</main>")[0]


def test_venue_page_lists_only_its_shows(conn):
    save(conn, show("Musk", at(18, 12)), show("Pulse", at(18, 20), "The Cinematheque"))
    page = pages(conn, NOW)["theatres/rio/index.html"]
    main = page.split("<main>")[1]
    assert "Musk" in main and "Pulse" not in main
    assert "1660 East Broadway" in page
    assert 'aria-current="page"' in page


def test_empty_venue_page_points_to_its_website(conn):
    page = pages(conn, NOW)["theatres/dunbar/index.html"]
    assert "Nothing listed here right now" in page


def test_titles_are_escaped(conn):
    save(conn, show("<script>alert(1)</script>", at(18, 12)))
    built = pages(conn, NOW)
    pages_only = [html for path, html in built.items() if path.endswith(".html")]
    assert all("<script>alert(1)</script>" not in html for html in pages_only)
    # films.json carries finished cards, escaped like every page
    assert "<script>alert(1)</script>" not in built["films.json"]
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in built["films.json"]


def test_stats(conn):
    save(conn, show("The Thing", at(31, 17), "VIFF Centre", year=1982), show("SFMF", at(18, 13)))
    db.save_film(conn, film("The Thing", 1982, 1091, letterboxd_rating=4.35))
    summary = stats(conn, NOW)
    assert summary["screenings"] == 2
    assert summary["per_venue"] == {"The Rio": 1, "VIFF Centre": 1}
    assert (summary["matched"], summary["with_letterboxd_rating"]) == (1, 1)
    assert summary["unmatched"] == ["sfmf|"]


def test_past_screenings_are_left_out(conn):
    save(conn, show("Gone", NOW - timedelta(hours=1)), show("Musk", at(18, 12)))
    assert "Gone" not in pages(conn, NOW)["index.html"]


# Home page rails


def rail_titles(conn, now=NOW):
    screenings = db.upcoming(conn, now)
    page_of = film_pages(screenings, db.films_by_key(conn))
    film_list = list({id(p): p for p in page_of.values()}.values())
    return {r.title: r for r in rails(film_list, now, db.venue_debuts(conn))}


@pytest.mark.parametrize(
    "n, expected",
    [(999, "999"), (1_000, "1K"), (12_345, "12K"), (1_476_132, "1.5M"), (2_000_000, "2M")],
)
def test_compact(n, expected):
    assert compact(n) == expected


def test_on_soon_lists_films_by_their_next_showing(conn):
    save(conn, show("Late", at(18, 21)), show("Early", at(18, 13)), show("Tomorrow", at(19, 13)))
    soon = rail_titles(conn)["On soon"]
    assert [(c.page.title, c.next_label) for c in soon.cards] == [
        ("Early", "Today, 1:00 pm"),
        ("Late", "Today, 9:00 pm"),
        ("Tomorrow", "Tomorrow, 1:00 pm"),
    ]
    assert soon.more == "showtimes/" and soon.live


def test_on_soon_gives_the_browser_every_showtime(conn):
    save(conn, show("Jaws", at(18, 13)), show("Jaws", at(20, 19)))
    home = pages(conn, NOW)["index.html"]
    assert 'data-live data-length="15"' in home
    assert 'data-times="2026-10-18T13:00 2026-10-20T19:00"' in home
    assert "static/onsoon.js" in home


def test_just_added_skips_the_first_run(conn):
    save(conn, show("Old", at(20, 19)))
    assert "Just added" not in rail_titles(conn)
    later = NOW + timedelta(days=2)
    db.sync_venue(conn, "The Rio", [show("Old", at(20, 19)), show("New", at(22, 19))], later)
    assert [c.page.title for c in rail_titles(conn, later)["Just added"].cards] == ["New"]


def test_a_new_theatre_does_not_fill_just_added(conn):
    save(conn, show("Old", at(20, 19)))
    later = NOW + timedelta(days=2)
    db.sync_venue(conn, "The Rio", [show("Old", at(20, 19)), show("New", at(22, 19))], later)
    db.sync_venue(conn, "The Dunbar", [show("Dunbar debut", at(23, 19), venue="The Dunbar")], later)
    titles = [c.page.title for c in rail_titles(conn, later)["Just added"].cards]
    assert titles == ["New"]


def test_highly_rated_needs_enough_ratings(conn):
    save(conn, show("Classic", at(18, 19), year=1970), show("Obscure", at(18, 20), year=2020))
    db.save_film(conn, film("Classic", 1970, 1, letterboxd_rating=4.1, letterboxd_count=50_000))
    db.save_film(conn, film("Obscure", 2020, 2, letterboxd_rating=4.9, letterboxd_count=12))
    found = rail_titles(conn)
    assert [c.page.title for c in found["Highly rated"].cards] == ["Classic"]
    assert found["Highly rated"].cards[0].why is None
    assert found["Highly rated"].cards[0].next_label == "Today, 7:00 pm"
    assert found["Highly rated"].more == "films/?sort=rated"


def test_special_screenings_rail(conn):
    save(
        conn,
        show("Dune: Part Three", at(18, 19), "The Park", note="Presented in 70mm"),
        Screening("The Cinematheque", "Vertigo", at(19, 19), "https://x", film_format="35mm"),
        show("Musk", at(18, 12), year=2026),
    )
    special = rail_titles(conn)["Special screenings"]
    # Every film here is already in On soon, so the row keeps its own order.
    assert [(c.page.title, c.why) for c in special.cards] == [
        ("Dune: Part Three", "70mm"),
        ("Vertigo", "35mm"),
    ]
    assert special.more == "films/?tag=special"


def test_empty_rails_are_left_out(conn):
    save(conn, show("Musk", at(22, 12), year=2026))
    assert set(rail_titles(conn)) == {"On soon"}


def test_leaving_soon_lists_films_whose_last_showing_is_this_week(conn):
    save(
        conn,
        show("Stays", at(19, 19)),
        show("Stays", at(30, 19)),
        show("Goes Later", at(19, 19)),
        show("Goes Later", at(23, 19)),
        show("Goes", at(19, 14)),
        show("Goes", at(20, 19)),
        show("Once", at(19, 19)),
    )
    rail = rail_titles(conn)["Leaving soon"]
    # A single screening isn't a run that's ending, so "Once" stays out
    assert [c.page.title for c in rail.cards] == ["Goes", "Goes Later"]
    assert rail.cards[0].why == "Ends Tue 20"
    assert rail.cards[0].next_label == "Ends Tue 20"
    assert rail.more == "films/?sort=leaving"


def test_a_film_shows_once_on_home_unless_a_row_would_be_too_short(conn):
    save(
        conn,
        *[show(f"Film {i:02}", at(18, 8 + i)) for i in range(15)],
        *[show(f"Doc {i}", at(21, 19 + i), note="Q&A") for i in range(3)],
    )
    found = rail_titles(conn)
    assert len([c for c in found["On soon"].cards[:15] if c.page.title.startswith("Film")]) == 15
    assert [c.page.title for c in found["Special screenings"].cards] == ["Doc 0", "Doc 1", "Doc 2"]


# Tags and filters


def test_rows_carry_tags_and_filter_data(conn):
    save(conn, show("Dune: Part Three", at(18, 19), "The Park", note="Presented in 70mm"))
    page = pages(conn, NOW)["showtimes/index.html"]
    assert 'data-venues="park"' in page
    assert 'data-tags="one-screening 70mm on-film special"' in page
    assert 'class="tag tag-format tag-70mm">70mm<' in plain(page)
    assert 'data-value="on-film"' in page


def test_a_single_screening_highlights_its_time_instead_of_a_badge(conn):
    save(conn, show("Twice", at(18, 19)), show("Twice", at(19, 19)), show("Once", at(18, 21)))
    page = pages(conn, NOW)["showtimes/index.html"]
    assert "tag-one-screening" not in page
    assert page.count("time time-rare") == 1
    assert page.count('data-tags="one-screening"') == 1


def test_film_page_shows_tags(conn):
    save(
        conn, Screening("The Cinematheque", "Vertigo", at(19, 19), "https://x", film_format="35mm")
    )
    page = pages(conn, NOW)["films/vertigo/index.html"]
    assert ">35mm<" in page and ">One screening<" not in page
    assert "time time-rare" in page


def test_every_rail_has_a_see_all_link(conn):
    save(conn, show("Lots", at(18, 19)), show("Lots", at(19, 19)), show("Soon", at(20, 19)))
    for rail in rail_titles(conn).values():
        assert rail.more.startswith(("films/", "showtimes/")), rail.title


def test_next_label_for_later_days(conn):
    save(conn, show("Later", at(24, 20, 15)))
    [card] = rail_titles(conn)["On soon"].cards
    assert card.next_label == "Saturday 24, 8:15 pm"


def test_home_cards_show_year_score_next_showing_and_theatres(conn):
    save(
        conn,
        show("Classic", at(18, 19), year=1970),
        show("Classic", at(20, 19), "The Cinematheque", year=1970),
    )
    db.save_film(conn, film("Classic", 1970, 1, letterboxd_rating=4.1, letterboxd_count=50_000))
    home = pages(conn, NOW)["index.html"]
    assert '<span class="year">1970</span>' in home
    assert 'title="4.1 on Letterboxd"' in home
    assert (
        '<span class="card-next"><time datetime="2026-10-18T19:00" data-fmt="card">'
        "Today, 7:00 pm</time></span>"
    ) in plain(home)
    assert (
        '<span class="where-rio">Rio</span><span class="where-cinematheque">Cinematheque</span>'
        in home
    )


def test_films_page_lists_each_film_once_with_dates(conn):
    save(conn, show("Musk", at(18, 12)), show("Musk", at(25, 12)), show("Pulse", at(19, 20)))
    page = pages(conn, NOW)["films/index.html"]
    assert page.count('<li class="card"') == 2
    assert "Oct 18 to Oct 25" in page
    assert 'class="card-next rare" title="One screening">' in page
    assert "Monday 19 only" in page
    assert 'data-sort-screenings="2"' in page


def test_films_page_genre_filter_data(conn):
    save(conn, show("The Thing", at(18, 20), year=1982))
    db.save_film(conn, film("The Thing", 1982, 1091, genres="Horror, Science Fiction"))
    page = pages(conn, NOW)["films/index.html"]
    assert 'data-genres="horror science-fiction"' in page
    assert 'data-value="science-fiction"' in page


def test_theatres_page_lists_every_venue(conn):
    page = pages(conn, NOW)["theatres/index.html"]
    for name in ("The Rio", "The Park", "The Cinematheque", "VIFF Centre", "The Dunbar"):
        assert name in page


def test_theatre_page_has_specs_and_about(conn):
    page = pages(conn, NOW)["theatres/cinematheque/index.html"]
    assert "<dt>Seats</dt><dd>153</dd>" in plain(page)
    assert "Pacific Cinémathèque Pacifique Society" in page


def test_badge_falls_back_to_initials_without_a_logo(conn, monkeypatch, tmp_path):
    import shutil

    import cineclock.render as render

    # A static folder with the stylesheet but no logo files
    for path in [*render.STATIC.glob("*.js"), render.STATIC / "site.css"]:
        shutil.copy(path, tmp_path / path.name)
    monkeypatch.setattr(render, "STATIC", tmp_path)
    save(conn, show("Musk", at(18, 12)))
    page = pages(conn, NOW)["films/index.html"]
    assert '<span class="monogram" role="img" aria-label="The Rio">R</span>' in page


def test_cards_show_letterboxd_before_any_other_score(conn):
    save(conn, show("Alien", at(18, 20), year=1979), show("Obscure", at(18, 21), year=2001))
    db.save_film(conn, film("Alien", 1979, 1, letterboxd_rating=4.2, imdb_rating=8.5))
    db.save_film(conn, film("Obscure", 2001, 2, rt_score=71, metacritic=60))
    page = pages(conn, NOW)["films/index.html"]
    assert 'title="4.2 on Letterboxd"' in page and "8.5" not in page
    assert 'title="71% on Rotten Tomatoes"' in page


def test_ratings_show_on_listings_without_toggles(conn):
    save(conn, show("The Thing", at(18, 20), year=1982))
    db.save_film(conn, film("The Thing", 1982, 1091, imdb_rating=8.2, metacritic=57))
    page = pages(conn, NOW)["showtimes/index.html"]
    # Listings carry one score: Letterboxd, or the next source that has one.
    assert 'class="card-rating card-rating-imdb" title="8.2 on IMDb"' in page
    assert "metacritic" not in page.split("<footer")[0].lower()
    film_page = pages(conn, NOW)["films/the-thing-1982/index.html"]
    assert "IMDb" in film_page
    assert "rating-switches" not in page


def test_viff_rooms_share_one_venue_and_label_each_room(conn):
    save(
        conn,
        show("NAZA", at(19, 16), "VIFF Centre", room="viff-main"),
        show("NAZA", at(20, 16), "VIFF Centre", room="viff-lochmaddy"),
    )
    [p] = {id(x): x for x in film_pages(db.upcoming(conn, NOW), {}).values()}.values()
    assert [v.name for v in p.venues] == ["VIFF Centre"]
    page = pages(conn, NOW)["films/naza/index.html"]
    assert page.count("<small>Lochmaddy</small>") == 1
    assert page.count("<small>VIFF Cinema</small>") == 1


def test_every_theatre_page_shows_the_same_spec_fields(conn):
    built = pages(conn, NOW)
    for v in VENUES:
        page = built[f"theatres/{v.slug}/index.html"]
        specs = "".join(re.findall(r'<dl class="specs">(.*?)</dl>', plain(page), re.S))
        assert re.findall(r"<dt>(\w+)</dt>", specs) == shown_specs() * len(v.rooms), v.name


def test_viff_rooms_are_swipe_cards(conn):
    page = pages(conn, NOW)["theatres/viff-centre/index.html"]
    assert 'class="room-swipe"' in page and "<select" not in page
    assert '<h3 class="room-name">Lochmaddy Studio Theatre</h3>' in page
    assert "<dt>Seats</dt><dd>41</dd>" in plain(page)
    rio = pages(conn, NOW)["theatres/rio/index.html"]
    assert 'class="room-single"' in rio and "room-name" not in rio


def test_theatre_page_uses_its_full_logo(conn):
    page = pages(conn, NOW)["theatres/park/index.html"]
    assert 'class="theatre-full-logo" src="../../static/logos/park-full.png"' in page


def test_theatre_logos_get_about_the_same_area():
    from cineclock.render import logo_size

    sizes = [logo_size(f"logos/{v.mark}", 900, 12) for v in VENUES if v.mark_scale == 1]
    areas = [w * h for w, h in sizes]
    # Wide logos would drop below the minimum height, so allow some spread
    assert max(areas) / min(areas) < 1.3
    assert all(h >= 12 for _, h in sizes)


def test_showtimes_use_the_logo_as_the_theatre_name(conn):
    save(conn, show("Musk", at(18, 12)))
    page = pages(conn, NOW)["showtimes/index.html"]
    heading = page.split('class="venue-heading"')[1].split("</h3>")[0]
    assert 'alt="The Rio"' in heading
    assert ">The Rio<" not in heading


def test_theatres_list_uses_full_logos(conn):
    page = pages(conn, NOW)["theatres/index.html"]
    assert 'src="../static/logos/rio-full.png"' in page
    assert "rio-mark.png" not in page


def test_home_has_the_tagline(conn):
    save(conn, show("Musk", at(18, 12)))
    page = pages(conn, NOW)["index.html"]
    assert '<p class="tagline">Support local cinemas.</p>' in page
    assert "<title>cineclock: Support local cinemas</title>" in page


def test_several_showings_on_one_day_are_not_flagged(conn):
    save(conn, show("Jaws", at(19, 14), year=1975), show("Jaws", at(19, 19), year=1975))
    [p] = {id(x): x for x in film_pages(db.upcoming(conn, NOW), {}).values()}.values()
    assert p.tags == []


def test_about_page_and_footer_links(conn):
    built = pages(conn, NOW)
    about = built["about/index.html"]
    assert "Hi, I'm Jordan." in about
    assert 'href="https://boxd.it/7T1D"' in about
    assert 'href="https://github.com/yogzoth"' in about
    home = built["index.html"]
    assert 'href="about/"' in home and 'href="https://boxd.it/7T1D"' in home


def test_theatre_page_has_a_photo_header_and_offer_pills(conn, photos):
    page = pages(conn, NOW)["theatres/park/index.html"]
    assert 'class="theatre-hero"' in page and "static/theatres/park.jpg" in page
    assert 'class="tag tag-format tag-70mm">70mm<' in plain(page)


def test_theatre_pills_include_what_is_on_its_schedule(conn):
    save(
        conn, show("Gremlins", at(19, 19), note="Q&A with Zach Galligan"), show("Jaws", at(20, 19))
    )
    page = plain(pages(conn, NOW)["theatres/rio/index.html"])
    head = page.split('class="theatre-offers"')[1].split("</p>")[0]
    assert ">Q&amp;A<" in head and ">7.1<" in head
    assert "One screening" not in head


def test_calendar_months_link_days_with_showings():
    from cineclock.listings import calendar_months

    shows = [show("Jaws", at(18, 19)), show("Jaws", at(31, 19))]
    days = group_days(shows, film_pages(shows, {}), {}, NOW)
    [october] = calendar_months(days, NOW)
    assert october.title == "October 2026"
    cells = [d for week in october.weeks for d in week if d]
    assert [d.date.day for d in cells if d.anchor] == [18, 31]
    assert [d.date.day for d in cells if d.today] == [18]
    assert october.weeks[0][:4] == [None] * 4  # Oct 1, 2026 is a Thursday


def test_showtimes_page_has_the_calendar(conn):
    save(conn, show("Jaws", at(19, 19)))
    page = pages(conn, NOW)["showtimes/index.html"]
    assert 'class="cal-pop"' in page and 'href="#d20261019"' in page


def test_spotlight_prefers_tonights_best_rated_film_with_art(conn):
    from cineclock.shelves import spotlight

    save(conn, show("Jaws", at(18, 19)), show("Alien", at(18, 21)), show("Later", at(19, 19)))
    db.save_film(conn, film("Jaws", None, 1, backdrop_url="https://b/j", letterboxd_rating=4.0))
    db.save_film(conn, film("Alien", None, 2, backdrop_url="https://b/a", letterboxd_rating=4.3))
    db.save_film(conn, film("Later", None, 3, backdrop_url="https://b/l", letterboxd_rating=4.9))
    page_of = film_pages(db.upcoming(conn, NOW), db.films_by_key(conn))
    assert spotlight(list({id(p): p for p in page_of.values()}.values()), NOW).title == "Alien"


def test_hero_leads_with_tonights_pick_then_each_next_days(conn):
    from cineclock.shelves import leads

    save(
        conn,
        show("Alien", at(18, 21)),
        show("Vertigo", at(18, 22)),
        show("Jaws", at(19, 19)),
        show("Jaws", at(19, 21, 30)),
        show("No Art", at(20, 19)),
    )
    for i, (title, score) in enumerate([("Alien", 4.3), ("Vertigo", 4.1), ("Jaws", 4)]):
        db.save_film(
            conn, film(title, None, i, backdrop_url=f"https://b/{i}", letterboxd_rating=score)
        )
    page_of = film_pages(db.upcoming(conn, NOW), db.films_by_key(conn))
    found = leads(list({id(p): p for p in page_of.values()}.values()), NOW)
    assert [(s.page.title, s.label) for s in found] == [
        ("Alien", "Tonight"),
        ("Vertigo", "Tonight"),
        ("Jaws", "Tomorrow"),
    ]
    # A pick stands until its last showing that day starts.
    assert found[2].until == at(19, 21, 30) and "7:00" in found[2].when


def test_hero_slides_say_when_and_for_how_long(conn):
    from cineclock.shelves import Slide

    save(
        conn,
        show("Jaws", at(19, 19)),
        show("Jaws", at(19, 21)),
        show("Heat", at(20, 19)),
        show("Heat", at(28, 19)),
        show("Ran", at(30, 19)),
        show("Ran", at(4, 19) + timedelta(days=31)),
    )
    page_of = film_pages(db.upcoming(conn, NOW), db.films_by_key(conn))
    run = {p.title: Slide(p, "", "", built=NOW).run for p in page_of.values()}
    assert run == {
        "Jaws": "2 showings, one day only",
        "Heat": "Playing Oct 20 to Oct 28",
        "Ran": "Playing Oct 30 to Nov 4",
    }


def test_home_lists_every_hero_candidate_for_the_page_to_pick(conn):
    save(conn, show("Alien", at(18, 21)), show("Jaws", at(19, 19)))
    for i, title in enumerate(["Alien", "Jaws"]):
        db.save_film(conn, film(title, None, i, backdrop_url=f"https://b/{i}", letterboxd_rating=4))
    home = pages(conn, NOW)["index.html"]
    assert home.count('class="spotlight is-lead"') == 2 and home.count('class="spotlight"') == 2
    assert "hero.js" in home and 'data-until="2026-10-19' in home


def test_a_note_every_showing_shares_moves_up_top(conn):
    note = "With Filmmakers Q&A"
    save(conn, show("Traces", at(25, 10), note=note), show("Traces", at(26, 10), note=note))
    [p] = {id(x): x for x in film_pages(db.upcoming(conn, NOW), {}).values()}.values()
    assert p.shared_note == note


def test_popular_leaves_out_what_highly_rated_shows(conn):
    save(conn, show("Classic", at(18, 19), year=1970), show("Blockbuster", at(18, 20), year=2020))
    db.save_film(conn, film("Classic", 1970, 1, letterboxd_rating=4.1, letterboxd_count=90_000))
    db.save_film(conn, film("Blockbuster", 2020, 2, letterboxd_rating=3.2, letterboxd_count=50_000))
    found = rail_titles(conn)
    assert [c.page.title for c in found["Highly rated"].cards] == ["Classic", "Blockbuster"]
    # Both are highly rated here, so nothing is left for Popular
    assert "Popular" not in found


def test_theatre_page_frames_the_inside_photo_beside_the_specs(conn, photos):
    page = pages(conn, NOW)["theatres/rio/index.html"]
    assert 'class="room-photo"' in page and "static/rooms/rio-main.jpg" in page
    viff = pages(conn, NOW)["theatres/viff-centre/index.html"]
    assert "static/rooms/viff-main.jpg" in viff and "static/rooms/viff-lochmaddy.jpg" in viff


def test_theatre_page_has_the_date_picker(conn):
    save(conn, show("Jaws", at(19, 19)), show("Jaws", at(20, 19)))
    page = pages(conn, NOW)["theatres/rio/index.html"]
    assert 'class="cal-pop"' in page and "static/calendar.js" in page


def test_film_without_a_poster_shows_its_title_in_the_frame(conn):
    save(conn, show("Obscure Thing", at(19, 19)))
    page = pages(conn, NOW)["films/index.html"]
    assert '<span class="poster-fallback-title">Obscure Thing</span>' in page
    # Each title keeps its own colour from build to build.
    hues = re.findall(r'class="poster-fallback" aria-hidden="true" style="--hue: (\d+)"', page)
    assert hues and all(0 <= int(h) < 360 for h in hues)


def test_film_page_groups_days_under_their_theatre(conn):
    save(conn, show("Jaws", at(19, 19)), show("Jaws", at(19, 21)), show("Jaws", at(20, 19)))
    page = pages(conn, NOW)["films/jaws/index.html"]
    assert page.count('class="showing"') == 2
    assert page.count('class="venue-group panel"') == 1


def test_film_page_shows_each_theatre_once(conn):
    save(
        conn,
        show("Jaws", at(19, 19)),
        show("Jaws", at(20, 19), "The Park"),
        show("Jaws", at(21, 19)),
        show("Jaws", at(22, 19)),
    )
    page = pages(conn, NOW)["films/jaws/index.html"]
    blocks = page.split('class="venue-group panel"')[1:]
    assert [b.count('class="showing"') for b in blocks] == [3, 1]
    assert "theatres/rio/" in blocks[0] and "theatres/park/" in blocks[1]


def test_theatre_rails_always_fill_and_topics_need_enough_films(conn):
    from cineclock.shelves import theatre_rails
    from cineclock.venues import VENUES

    dunbar = next(v for v in VENUES if v.slug == "dunbar")
    rio = next(v for v in VENUES if v.slug == "rio")
    save(
        conn,
        show("Small A", at(19, 19), venue="The Dunbar"),
        show("Small B", at(20, 19), venue="The Dunbar"),
        *[show(f"Print {i}", at(19 + i, 19), note="35mm") for i in range(3)],
        show("Long Run", at(19, 21), year=1999),
        show("Long Run", at(28, 21), year=1999),
    )
    page_of = film_pages(db.upcoming(conn, NOW), db.films_by_key(conn))
    film_list = list({id(p): p for p in page_of.values()}.values())
    debuts = db.venue_debuts(conn)

    small = theatre_rails(dunbar, film_list, NOW, debuts)
    assert [(r.title, len(r.cards)) for r in small] == [("Now playing", 2)]
    assert small[0].more == "films/?venue=dunbar"

    big = theatre_rails(rio, film_list, NOW, debuts)
    assert [(r.title, len(r.cards)) for r in big] == [("Now playing", 4), ("Special screenings", 3)]
    assert big[1].more == "films/?venue=rio&sort=next&tag=special"
    page = pages(conn, NOW)["theatres/rio/index.html"]
    assert 'class="theatre-rails"' in page and ">Now playing<" in page


def test_home_says_when_listings_were_updated(conn):
    save(conn, show("Jaws", at(19, 19)))
    home = pages(conn, NOW)["index.html"]
    assert 'class="freshness"' in home
    assert 'Updated <span class="freshness-day">Sun Oct 18, </span>1:00 am' in home


def test_link_previews_have_a_title_blurb_and_picture(conn, photos):
    save(
        conn, show("Alien", at(19, 19), year=1979), show("Alien", at(21, 19), "The Park", year=1979)
    )
    db.save_film(
        conn, film("Alien", 1979, 1, backdrop_url="https://img/alien.jpg", letterboxd_rating=4.3)
    )
    built = pages(conn, NOW)
    home = built["index.html"]
    assert (
        '<meta property="og:image" content="https://cineclock.ca/static/brand/share.png">' in home
    )
    assert '<meta property="og:url" content="https://cineclock.ca/">' in home
    assert (
        "The Rio, The Park" in home and 'name="twitter:card" content="summary_large_image"' in home
    )

    film_page = built["films/alien-1979/index.html"]
    assert '<meta property="og:title" content="Alien (1979) · ★ 4.3">' in film_page
    assert '<meta property="og:image" content="https://img/alien.jpg">' in film_page
    assert 'content="Oct 19 to Oct 21 at The Rio and The Park.' in film_page

    rio = built["theatres/rio/index.html"]
    assert "static/theatres/rio.jpg" in rio


def test_watchlist_shelf_cards_come_ready_made_in_films_json(conn):
    import json

    save(conn, show("Jaws", at(19, 19), year=1975))
    db.save_film(conn, film("Jaws", 1975, 1, letterboxd_rating=4.0))
    [entry] = json.loads(pages(conn, NOW)["films.json"])
    assert entry["card"].startswith('<li class="card" data-film="jaws-1975"')
    assert 'href="@root@films/jaws-1975/"' in entry["card"]
    assert (
        '<span class="year">1975</span>' in entry["card"] and "4.0 on Letterboxd" in entry["card"]
    )


def test_a_special_screening_has_a_badge_on_its_poster(conn):
    save(conn, show("SFMF Series 2", at(19, 19), note="Shorts"), show("Jaws", at(19, 21)))
    db.save_film(conn, film("Jaws", None, 1, letterboxd_rating=4.0))
    page = plain(pages(conn, NOW)["films/index.html"])
    assert 'class="card-badge" title="Shorts">Shorts<' in page
    assert page.count('class="card-badge"') == 1


def test_a_festival_series_tags_its_screenings(conn):
    save(
        conn,
        Screening(
            "The Cinematheque", "SFMF Series 2", at(19, 13), "https://x",
            series="Small File Media Festival 2026",
        ),
    )  # fmt: skip
    page = plain(pages(conn, NOW)["films/index.html"])
    assert 'class="card-badge" title="Festival">Festival<' in page


def test_a_bare_listing_gets_the_general_event_tag(conn):
    save(
        conn, show("Mystery Night", at(19, 19)), show("Jaws", at(20, 19)), show("Jaws", at(21, 19))
    )
    db.save_film(conn, film("Jaws", None, 1, letterboxd_rating=4.0))
    pages_by = {
        p.title: p for p in film_pages(db.upcoming(conn, NOW), db.films_by_key(conn)).values()
    }
    assert pages_by["Mystery Night"].tags == ["One screening", "Event"]
    assert pages_by["Jaws"].tags == []
    page = plain(pages(conn, NOW)["films/index.html"])
    assert 'class="card-badge" title="Event">Event<' in page


def test_a_film_with_no_score_just_leaves_it_out(conn):
    save(conn, show("New Doc", at(19, 19), year=2026), show("New Doc", at(20, 19), year=2026))
    card = plain(pages(conn, NOW)["films/index.html"]).split('<li class="card"', 1)[1]
    assert '<span class="year">2026</span>' in card
    assert "card-rating" not in card.split("</li>")[0] and "Unrated" not in card


def test_the_event_tag_is_not_a_second_events_chip(conn):
    save(conn, show("Mystery Night", at(19, 19)))
    page = pages(conn, NOW)["films/index.html"]
    assert page.count('data-value="event"') == 1
    assert 'data-tags="one-screening event special"' in page


def test_film_page_poster_opens_big(conn):
    save(conn, show("Alien", at(19, 19), year=1979))
    db.save_film(conn, film("Alien", 1979, 1, poster_url="https://image.tmdb.org/t/p/w500/a.jpg"))
    page = pages(conn, NOW)["films/alien-1979/index.html"]
    assert '<a class="poster-zoom" href="https://image.tmdb.org/t/p/w780/a.jpg"' in page
    assert '<dialog class="poster-dialog"' in page


def test_date_strip_names_the_month_once_it_changes():
    today = date(2026, 10, 18)
    assert short_day(date(2026, 10, 20), today) == "Tue 20"
    assert short_day(date(2026, 11, 1), today) == "Sun Nov 1"


def test_film_page_falls_back_to_the_theatres_description(conn):
    blurb = "A live score by Applied Silence for Dreyer's dreamlike 1932 horror classic."
    save(
        conn,
        Screening(
            "The Cinematheque", "Vampyr x Applied Silence", at(31, 20), "https://x", synopsis=blurb
        ),
    )
    page = pages(conn, NOW)["films/vampyr-x-applied-silence/index.html"]
    assert f'<p class="synopsis">{blurb}</p>' in page.replace("&#39;", "'")


def test_a_card_with_a_score_still_shows_its_badge(conn):
    save(conn, show("Vertigo", at(19, 19), year=1958, note="35mm"))
    db.save_film(conn, film("Vertigo", 1958, 1, letterboxd_rating=4.2))
    page = plain(pages(conn, NOW)["films/index.html"])
    assert 'class="card-badge" title="35mm">35mm<' in page
    assert 'title="4.2 on Letterboxd"' in page
    # When it's on, then where, in the box under the poster.
    card = page.split('<li class="card"', 1)[1]
    assert card.index("card-badge") < card.index("card-next") < card.index("card-where")


def test_no_card_has_an_empty_score_spot(conn):
    save(conn, show("Five Films by Somebody", at(19, 19), note="Shorts"))
    page = plain(pages(conn, NOW)["films/index.html"])
    assert 'class="card-badge" title="Shorts">Shorts<' in page


def test_theatre_pages_fold_away_prices_and_specs(conn):
    built = pages(conn, NOW)
    viff = plain(built["theatres/viff-centre/index.html"])
    assert '<span class="fold-title">Tickets</span>' in viff
    assert "<dt>Adult</dt><dd>$17 / $15</dd>" in viff
    assert '<span class="fold-title">Tech specs</span>' in viff and "2 rooms" in viff
    assert 'href="https://viff.org/ticket-info/"' in viff and ">https://" not in viff
    # The Dunbar's prices come from local guides, and the page says so.
    dunbar = plain(built["theatres/dunbar/index.html"])
    assert "<dt>Cheap Tuesday<small>All day, everyone</small></dt><dd>$8.50</dd>" in dunbar
    assert "not by the Dunbar itself" in dunbar and "own website" not in dunbar


def test_theatre_tags_link_to_films_at_that_theatre(conn):
    save(conn, show("Gremlins", at(19, 19), year=1984, note="Live Q&A with Zach Galligan"))
    rio = pages(conn, NOW)["theatres/rio/index.html"]
    assert 'href="../../films/?venue=rio&amp;tag=q-a"' in rio
    # Room features aren't on any screening, so they stay plain pills.
    assert '<span class="tag tag-event tag-7-1">' in rio


def test_booking_opens_in_a_new_tab(conn):
    save(conn, show("Jaws", at(19, 19), year=1975))
    page = pages(conn, NOW)["showtimes/index.html"]
    assert 'target="_blank" rel="noopener" aria-label="Tickets for Jaws' in page


def test_date_labels_carry_their_real_date_for_the_browser(conn):
    # The build's "Today" goes stale overnight, so each label keeps its date for when.js.
    save(conn, show("Late", at(18, 21)), show("Next", at(19, 13)))
    built = pages(conn, NOW)
    showtimes = plain(built["showtimes/index.html"])
    assert '<time datetime="2026-10-18" data-fmt="day">Today, Oct 18</time>' in showtimes
    assert '<time datetime="2026-10-19" data-fmt="short">Tomorrow</time>' in showtimes
    assert 'data-day="2026-10-19"' in showtimes
    assert "static/when.js?v=" in built["index.html"]


def test_home_has_a_heading_and_room_swipe_is_keyboard_reachable(conn):
    save(conn, show("Musk", at(18, 12)))
    built = pages(conn, NOW)
    assert "<h1" in built["index.html"]
    viff = built["theatres/viff-centre/index.html"]
    assert 'class="room-swipe" tabindex="0" role="region" aria-label="Rooms"' in viff
    assert 'tabindex="0"' not in built["theatres/rio/index.html"].split("room-single")[1][:40]


def test_every_photo_has_an_author_and_a_known_licence():
    from cineclock.venues import LICENCES

    photos = [v.photo for v in VENUES] + [r.photo for v in VENUES for r in v.rooms]
    for photo in filter(None, photos):
        if photo.placeholder:
            continue
        assert photo.author and photo.licence
        if photo.source_url:
            # An archive photo: its credit links to the archive's own record.
            assert photo.page_url == photo.source_url and photo.licence_url is None
        else:
            assert photo.licence in LICENCES
            assert photo.page_url.startswith("https://commons.wikimedia.org/wiki/File:")


def test_photos_show_only_with_their_credit(conn, photos):
    save(conn, show("Musk", at(18, 12)))
    built = pages(conn, NOW)

    rio = built["theatres/rio/index.html"]
    assert 'class="theatre-hero"' in rio and "static/rooms/rio-main.jpg" in rio
    assert rio.count('class="photo-credit"') == 2
    assert "Canmenwalker</a>" in rio and "CC BY 4.0</a>, toned" in rio
    # A stand-in photo says so instead of naming anyone.
    assert "Placeholder photo, source to be confirmed" in built["theatres/viff-centre/index.html"]
    assert "Rio photo:" in built["theatres/index.html"]


@pytest.fixture
def photos(monkeypatch, tmp_path):
    """A static folder holding a stand-in file for every credited photo."""
    import shutil

    import cineclock.render as render

    shutil.copytree(render.STATIC, tmp_path, dirs_exist_ok=True)
    for folder in ("theatres", "rooms"):
        (tmp_path / folder).mkdir(exist_ok=True)
    for venue in VENUES:
        if venue.photo:
            (tmp_path / "theatres" / f"{venue.slug}.jpg").write_bytes(b"jpg")
        for room in venue.rooms:
            if room.photo:
                (tmp_path / "rooms" / f"{room.id}.jpg").write_bytes(b"jpg")
    monkeypatch.setattr(render, "STATIC", tmp_path)
    return tmp_path


def test_sold_out_time_says_so_instead_of_tickets(conn):
    sold = Screening("VIFF Centre", "Cure", at(19, 19), "https://viff.org/cure/", status="Sold out")
    db.sync_venue(conn, "VIFF Centre", [sold], NOW)
    page = pages(conn, NOW)["films/cure/index.html"]
    assert 'class="time time-closed"' in page and "<small>Sold out" in page
    assert 'aria-label="Sold out: Cure' in page and "Tickets for Cure" not in page


def test_stats_name_theatres_that_keep_failing(conn):
    for _ in range(3):
        db.record_run(conn, "The Park", ok=False, now=NOW)
    db.record_run(conn, "The Rio", ok=True, now=NOW)
    db.record_run(conn, "The Dunbar", ok=False, now=NOW)
    summary = stats(conn, NOW)
    assert summary["stale"] == ["The Park"]
    assert summary["venues"]["The Rio"] == {"last_ok": NOW.isoformat(), "failures": 0}
    assert list(summary["venues"]) == ["The Rio", "The Park", "The Dunbar"]


def test_privacy_page_is_built_and_linked_from_every_page(conn):
    built = pages(conn, NOW)
    assert "Privacy" in built["privacy/index.html"]
    assert 'href="privacy/"' in built["index.html"]


def test_films_json_carries_every_score_and_the_imdb_id(conn):
    import json

    save(conn, show("Jaws", at(19, 19), year=1975))
    db.save_film(
        conn,
        film(
            "Jaws",
            1975,
            1,
            imdb_id="tt0073195",
            letterboxd_rating=4.0,
            letterboxd_count=900000,
            imdb_rating=8.1,
            rt_score=97,
            metacritic=87,
        ),
    )
    [entry] = json.loads(pages(conn, NOW)["films.json"])
    assert entry["imdb"] == "tt0073195" and entry["lb_count"] == 900000
    assert entry["scores"] == {"letterboxd": 4.0, "imdb": 8.1, "rt": 97, "metacritic": 87}
    assert "<b>97%</b>" in entry["rated"]["rt"] and "card-rating-rt" in entry["rated"]["rt"]
    # The card itself still leads with Letterboxd.
    assert "card-rating-letterboxd" in entry["card"]


def test_films_and_theatres_have_save_buttons(conn):
    save(conn, show("Jaws", at(19, 19), year=1975))
    built = pages(conn, NOW)
    assert 'data-save="film" data-ref="jaws-1975"' in built["films/jaws-1975/index.html"]
    assert 'data-save="theatre" data-ref="rio"' in built["theatres/rio/index.html"]
    assert "static/account.js" in built["index.html"]
    assert 'data-api="https://api.cineclock.ca"' in built["index.html"]


def test_account_page_offers_google_sign_in(conn):
    page = pages(conn, NOW)["me/index.html"]
    assert "accounts.google.com/gsi/client" in page
    assert "358607256767-mkorjtl09q7ii10nuhfo1fesjfcvgnnv.apps.googleusercontent.com" in page
    assert "dash-calendar" in page and "me-imdb-file" in page


def test_highly_rated_rail_has_the_id_the_rating_switch_looks_for(conn):
    save(conn, show("Jaws", at(19, 19), year=1975))
    db.save_film(conn, film("Jaws", 1975, 1, letterboxd_rating=4.0, letterboxd_count=900000))
    assert 'aria-labelledby="rail-highly-rated"' in pages(conn, NOW)["index.html"]


def test_a_theatre_without_a_photo_gets_the_designed_card(conn):
    page = pages(conn, NOW)["theatres/index.html"]
    assert page.count("theatre-card-art no-photo") == 0  # every theatre has one now


def test_every_page_has_the_header_account_link(conn):
    built = pages(conn, NOW)
    assert 'data-account-link aria-label="Sign in"' in built["films/index.html"]
    assert 'href="../me/" data-account-link' in built["films/index.html"]
    assert 'data-account-link aria-label="Sign in" aria-current="page"' in built["me/index.html"]


def test_alerts_feed_lists_each_screening_with_when_it_was_first_seen(conn):
    import json

    save(conn, show("Jaws", at(19, 19), year=1975), show("Jaws", at(21, 19), year=1975))
    db.save_film(conn, film("Jaws", 1975, 1, imdb_id="tt0073195"))
    [entry] = json.loads(pages(conn, NOW)["alerts.json"])
    assert entry["slug"] == "jaws-1975" and entry["imdb"] == "tt0073195"
    assert [s["venue"] for s in entry["screenings"]] == ["The Rio", "The Rio"]
    first, last = entry["screenings"]
    assert first["at"] < last["at"] and first["seen"] == NOW.isoformat()
    assert first["url"].startswith("http")


def test_account_page_has_alert_settings(conn):
    page = pages(conn, NOW)["me/index.html"]
    assert 'id="me-alerts-on"' in page and 'role="switch"' in page
    for name in ("last", "added", "imdb"):
        assert f'<input type="checkbox" name="{name}">' in page
    assert 'id="me-alerts-test"' in page


def test_home_invites_visitors_to_sign_in_for_their_watchlist(conn):
    save(conn, show("Jaws", at(19, 19)))
    home = pages(conn, NOW)["index.html"]
    assert 'class="signin-nudge panel" href="me/" data-signin-prompt hidden' in home
    assert "lb-button" not in home


def test_pages_move_http_and_github_io_visits_to_the_real_address(conn):
    page = pages(conn, NOW)["index.html"]
    assert 'location.replace("https://cineclock.ca"' in page


def test_card_crop_keeps_the_photo_covering_the_card():
    from cineclock.render import card_crop
    from cineclock.venues import VENUES

    for venue in VENUES:
        style = card_crop(venue)
        if not venue.sign_at:
            continue
        x, y = [
            float(n) for n in style.split("translate(")[1].split(")")[0].replace("%", "").split(",")
        ]
        floor = (1 - venue.sign_zoom) * 100
        assert floor <= x <= 0 and floor <= y <= 0, venue.slug


def test_hero_spreads_its_picks_across_the_theatres(conn):
    from cineclock.shelves import highlights

    save(
        conn,
        show("Alien", at(18, 21)),
        show("Jaws", at(19, 19)),
        show("Heat", at(19, 20)),
        show("Ran", at(20, 19), venue="The Park"),
    )
    for i, (title, score) in enumerate([("Alien", 4.6), ("Jaws", 4.5), ("Heat", 4.4), ("Ran", 3)]):
        db.save_film(
            conn, film(title, None, i, backdrop_url=f"https://b/{i}", letterboxd_rating=score)
        )
    page_of = film_pages(db.upcoming(conn, NOW), db.films_by_key(conn))
    slides = highlights(list({id(p): p for p in page_of.values()}.values()), NOW, count=3)
    # Ran is rated lowest but it's the Park's only film, so it beats a third Rio film.
    assert [s.page.title for s in slides] == ["Alien", "Ran", "Jaws"]


def test_films_json_knows_each_films_last_day(conn):
    import json

    save(conn, show("Jaws", at(19, 19)), show("Jaws", at(24, 21)))
    [entry] = json.loads(pages(conn, NOW)["films.json"])
    assert entry["last"] == "2026-10-24"


def test_films_json_lists_each_day_a_film_plays(conn):
    import json

    save(conn, show("Jaws", at(19, 19)), show("Jaws", at(19, 21)), show("Jaws", at(22, 19)))
    [entry] = json.loads(pages(conn, NOW)["films.json"])
    assert entry["days"] == ["2026-10-19", "2026-10-22"]


def test_theatre_page_jumps_to_its_showtimes(conn):
    save(conn, show("Jaws", at(19, 19)))
    page = pages(conn, NOW)["theatres/rio/index.html"]
    assert 'class="fold fold-jump" href="#showtimes"' in page
    assert 'id="showtimes"' in page and 'data-fmt="short">Tomorrow<' in page
