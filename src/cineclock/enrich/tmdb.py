"""The Movie Database: finds the film behind a venue title, with poster and synopsis.

This product uses the TMDB API but is not endorsed or certified by TMDB.
"""

from difflib import SequenceMatcher

import httpx

from cineclock.films import normalize, search_queries

API = "https://api.themoviedb.org/3"
POSTER = "https://image.tmdb.org/t/p/w500"
BACKDROP = "https://image.tmdb.org/t/p/w1280"


def auth(key: str) -> tuple[dict, dict]:
    """TMDB accepts the short API key as a parameter or the long read token as a header."""
    if key.startswith("eyJ"):
        return {}, {"Authorization": f"Bearer {key}"}
    return {"api_key": key}, {}


def release_year(result: dict) -> int | None:
    date = result.get("release_date") or ""
    return int(date[:4]) if date[:4].isdigit() else None


def similarity(result: dict, title: str) -> float:
    wanted = normalize(title)
    names = {normalize(result.get("title") or ""), normalize(result.get("original_title") or "")}
    return max(SequenceMatcher(None, wanted, name).ratio() for name in names)


def pick_match(results: list[dict], title: str, year: int | None) -> dict | None:
    """Choose the search result that is this film, or None if none clearly is.

    With a year, a close title within a year either way is enough (venues and
    TMDB sometimes disagree by one). Without a year the title must match
    exactly, and the most popular film with that title wins.
    """
    scored = []
    for result in results:
        score = similarity(result, title)
        found_year = release_year(result)
        if year:
            if found_year is None or abs(found_year - year) > 1 or score < 0.75:
                continue
            exact_year = found_year == year
            scored.append((score, exact_year, result.get("popularity") or 0, result))
        elif score == 1.0:
            scored.append((score, True, result.get("popularity") or 0, result))
    if not scored:
        return None
    return max(scored, key=lambda item: item[:3])[3]


def parse_details(data: dict) -> dict:
    directors = [
        p["name"] for p in data.get("credits", {}).get("crew", []) if p.get("job") == "Director"
    ]
    return {
        "tmdb_id": data["id"],
        "imdb_id": data.get("external_ids", {}).get("imdb_id") or data.get("imdb_id") or None,
        "title": data.get("title"),
        "year": release_year(data),
        "director": ", ".join(directors) or None,
        "runtime": data.get("runtime") or None,
        "overview": data.get("overview") or None,
        "genres": ", ".join(g["name"] for g in data.get("genres", [])) or None,
        "poster_url": POSTER + data["poster_path"] if data.get("poster_path") else None,
        "backdrop_url": BACKDROP + data["backdrop_path"] if data.get("backdrop_path") else None,
    }


def details(client: httpx.Client, key: str, tmdb_id: int) -> dict:
    params, headers = auth(key)
    response = client.get(
        f"{API}/movie/{tmdb_id}",
        params={**params, "append_to_response": "external_ids,credits"},
        headers=headers,
    )
    response.raise_for_status()
    return parse_details(response.json())


def find_by_imdb(client: httpx.Client, key: str, imdb_id: str) -> dict | None:
    """The exact film, when the theatre's page links its IMDb entry."""
    params, headers = auth(key)
    response = client.get(
        f"{API}/find/{imdb_id}",
        params={**params, "external_source": "imdb_id"},
        headers=headers,
    )
    response.raise_for_status()
    movies = response.json().get("movie_results") or []
    return details(client, key, movies[0]["id"]) if movies else None


def find(
    client: httpx.Client, key: str, title: str, year: int | None, imdb_id: str | None = None
) -> dict | None:
    if imdb_id:
        found = find_by_imdb(client, key, imdb_id)
        if found:
            return found
    params, headers = auth(key)
    for query in search_queries(title, year):
        response = client.get(
            f"{API}/search/movie",
            params={**params, "query": query, "include_adult": "false"},
            headers=headers,
        )
        response.raise_for_status()
        match = pick_match(response.json().get("results", []), query, year)
        if match:
            return details(client, key, match["id"])
    return None
