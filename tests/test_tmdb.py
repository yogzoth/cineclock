import httpx
import pytest

from cineclock.enrich import tmdb


def result(id, title, date, popularity=1.0, original=None):
    return {
        "id": id,
        "title": title,
        "original_title": original or title,
        "release_date": date,
        "popularity": popularity,
    }


def test_auth_with_short_key_and_long_token():
    assert tmdb.auth("abc123") == ({"api_key": "abc123"}, {})
    assert tmdb.auth("eyJhbGciOi.x.y") == ({}, {"Authorization": "Bearer eyJhbGciOi.x.y"})


def test_year_picks_the_right_remake(fixture_json):
    results = fixture_json("enrich/tmdb_search_the_thing.json")["results"]
    assert tmdb.pick_match(results, "The Thing", 1982)["id"] == 1091
    assert tmdb.pick_match(results, "The Thing", 2011)["id"] == 60935


def test_year_can_be_off_by_one():
    results = [result(1, "Daughters of Darkness", "1972-05-01")]
    assert tmdb.pick_match(results, "Daughters of Darkness", 1971)["id"] == 1


def test_exact_year_beats_off_by_one():
    results = [result(1, "Pulse", "2000-01-01", 99), result(2, "Pulse", "2001-02-03", 5)]
    assert tmdb.pick_match(results, "Pulse", 2001)["id"] == 2


def test_year_too_far_off_is_rejected():
    assert tmdb.pick_match([result(1, "Pulse", "2006-08-11")], "Pulse", 2001) is None


def test_original_title_counts():
    results = [result(1, "Daughters of Darkness", "1971-05-01", original="Les lèvres rouges")]
    assert tmdb.pick_match(results, "Les lèvres rouges", 1971)["id"] == 1


def test_loose_title_needs_a_year():
    results = [result(1, "The Devils", "1971-07-16")]
    assert tmdb.pick_match(results, "The Devil", 1971)["id"] == 1
    assert tmdb.pick_match(results, "The Devil", None) is None


def test_without_year_most_popular_exact_title_wins():
    results = [result(1, "Pulse", "2001-02-03", 10), result(2, "Pulse", "2006-08-11", 20)]
    assert tmdb.pick_match(results, "Pulse", None)["id"] == 2


def test_shorts_programs_do_not_match_random_films():
    results = [result(1, "Series 2", "2015-01-01", 50)]
    assert tmdb.pick_match(results, "SFMF Series 2", None) is None


def test_missing_release_date_is_skipped_when_year_given():
    assert tmdb.pick_match([result(1, "Pulse", "")], "Pulse", 2001) is None


def test_parse_details(fixture_json):
    details = tmdb.parse_details(fixture_json("enrich/tmdb_movie_1091.json"))
    assert details == {
        "tmdb_id": 1091,
        "imdb_id": "tt0084787",
        "title": "The Thing",
        "year": 1982,
        "director": "John Carpenter",
        "runtime": 109,
        "overview": details["overview"],
        "genres": "Horror, Mystery, Science Fiction",
        "poster_url": "https://image.tmdb.org/t/p/w500/tzGY49kseSE9QAKk47uuDGwnSCu.jpg",
        "backdrop_url": "https://image.tmdb.org/t/p/w1280/r9leYNa8nTRCceZrZhP1DXkgKVb.jpg",
    }
    assert details["overview"].startswith("A research team in Antarctica")


def test_parse_details_with_little_data():
    details = tmdb.parse_details({"id": 5, "title": "Obscure"})
    assert details["tmdb_id"] == 5
    assert details["poster_url"] is details["director"] is details["year"] is None
    assert details["genres"] is None


@pytest.fixture
def fake_tmdb(fixture_json):
    calls = []

    def handler(request):
        calls.append(request)
        if request.url.params.get("api_key") != "k":
            return httpx.Response(401)
        if request.url.path == "/3/search/movie":
            query = request.url.params["query"]
            if query == "The Thing":
                return httpx.Response(200, json=fixture_json("enrich/tmdb_search_the_thing.json"))
            return httpx.Response(200, json={"results": []})
        if request.url.path == "/3/movie/1091":
            return httpx.Response(200, json=fixture_json("enrich/tmdb_movie_1091.json"))
        return httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_find(fake_tmdb):
    client, calls = fake_tmdb
    found = tmdb.find(client, "k", "The Thing", 1982)
    assert found["tmdb_id"] == 1091
    assert calls[-1].url.params["append_to_response"] == "external_ids,credits"


def test_find_tries_the_title_inside_a_possessive(fake_tmdb):
    client, calls = fake_tmdb
    found = tmdb.find(client, "k", "John Carpenter's 'The Thing'", 1982)
    assert found["tmdb_id"] == 1091
    assert [c.url.params.get("query") for c in calls[:2]] == [
        "John Carpenter's 'The Thing'",
        "The Thing",
    ]


def test_find_nothing(fake_tmdb):
    client, _ = fake_tmdb
    assert tmdb.find(client, "k", "SFMF Series 2", None) is None


def test_bad_key_raises(fake_tmdb):
    client, _ = fake_tmdb
    with pytest.raises(httpx.HTTPStatusError):
        tmdb.find(client, "wrong", "The Thing", 1982)


def test_an_imdb_id_finds_the_exact_film_without_searching():
    asked = []

    def handler(request):
        asked.append(request.url.path)
        if request.url.path == "/3/find/tt36955518":
            assert request.url.params["external_source"] == "imdb_id"
            return httpx.Response(200, json={"movie_results": [{"id": 777}]})
        if request.url.path == "/3/movie/777":
            return httpx.Response(
                200, json={"id": 777, "title": "Jim Queen", "release_date": "2026-05-01"}
            )
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        found = tmdb.find(client, "key", "Jim Queen", None, "tt36955518")
    assert found["tmdb_id"] == 777 and found["year"] == 2026
    assert not any(path.startswith("/3/search") for path in asked)


def test_an_imdb_id_tmdb_doesnt_know_falls_back_to_searching():
    def handler(request):
        if request.url.path.startswith("/3/find/"):
            return httpx.Response(200, json={"movie_results": []})
        if request.url.path == "/3/search/movie":
            return httpx.Response(200, json={"results": []})
        return httpx.Response(500)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert tmdb.find(client, "key", "Nothing", None, "tt0000001") is None
