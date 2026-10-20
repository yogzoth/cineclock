import re
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, replace

import httpx

from cineclock.models import Screening

USER_AGENT = "cineclock (+https://github.com/yogzoth/cineclock)"


@dataclass(frozen=True)
class FilmDetails:
    year: int | None = None
    poster_url: str | None = None
    film_format: str | None = None
    series: str | None = None
    synopsis: str | None = None
    series_url: str | None = None


QUOTES = "\"'“‘«"


def clean_summary(text: str | None) -> str | None:
    """Tidy a theatre's description into a synopsis: one line of plain text, with
    any credits line run on at the end ("MUSK (Alex Gibney, 2026 / PG ...") cut off."""
    if not text:
        return None
    text = " ".join(text.split())
    credits = re.search(r"\s[A-Z0-9][A-Z0-9 :'’&!?.-]+ \([^)]*\b(18|19|20)\d{2}\b", text)
    if credits:
        text = text[: credits.start()].rstrip()
    # Excerpts cut off mid-sentence ("... Read more", "…") end at their last full sentence.
    cut = re.sub(r"\s*(\[?(\.\.\.|…)\]?)?\s*(Read more|Continue reading)\W*$", "…", text)
    if cut.endswith(("…", "...")):
        end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "), cut.rfind(".” "))
        cut = cut[: end + 1].rstrip() if end > 0 else cut
    text = cut
    return text if len(text) >= 40 else None


def page_summary(soup) -> str | None:
    """The short description a page gives for link previews and search results."""
    for attrs in ({"property": "og:description"}, {"name": "description"}):
        tag = soup.find("meta", attrs=attrs)
        if tag and (summary := clean_summary(tag.get("content"))):
            return summary
    return None


def first_paragraph(container) -> str | None:
    """The first real paragraph of a description, skipping pull quotes from reviews."""
    if not container:
        return None
    for p in container.find_all("p"):
        text = p.get_text(" ", strip=True)
        if text and text[0] not in QUOTES and (summary := clean_summary(text)):
            return summary
    return None


class PatientClient(httpx.Client):
    """An httpx client that tries again, after a pause, when a site is busy
    (429), briefly down (502, 503, 504) or slow to answer. Two more tries at
    most, so one bad theatre can't hold up the whole run for long."""

    RETRY_STATUS = {429, 502, 503, 504}
    WAITS = (2.0, 6.0)
    MAX_WAIT = 30.0
    sleep = staticmethod(time.sleep)

    def send(self, request, **kwargs):
        for wait in (*self.WAITS, None):
            try:
                response = super().send(request, **kwargs)
            except (httpx.TimeoutException, httpx.ConnectError):
                if wait is None:
                    raise
                self.sleep(wait)
                continue
            if response.status_code not in self.RETRY_STATUS or wait is None:
                return response
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                wait = min(float(retry_after), self.MAX_WAIT)
            response.close()
            self.sleep(wait)
        raise AssertionError("unreachable")


def make_client() -> httpx.Client:
    headers = {
        "User-Agent": USER_AGENT,
        # Some venue sites refuse requests that don't say they accept HTML.
        "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
    }
    return PatientClient(headers=headers, timeout=30, follow_redirects=True)


def add_details(
    client: httpx.Client,
    screenings: Iterable[Screening],
    parse_page: Callable[[str], FilmDetails],
) -> list[Screening]:
    """Fill in year and poster from each film's page, fetching every page only once."""
    screenings = list(screenings)
    details = {}
    for url in {s.info_url for s in screenings if s.info_url}:
        try:
            page = client.get(url)
            page.raise_for_status()
            details[url] = parse_page(page.text)
        except httpx.HTTPError:
            # A missing year or poster is fine; the screening still shows.
            details[url] = FilmDetails()

    return [
        replace(
            s,
            year=details[s.info_url].year or s.year,
            poster_url=details[s.info_url].poster_url or s.poster_url,
            film_format=details[s.info_url].film_format or s.film_format,
            series=details[s.info_url].series or s.series,
            series_url=details[s.info_url].series_url or s.series_url,
            synopsis=details[s.info_url].synopsis or s.synopsis,
        )
        if s.info_url
        else s
        for s in screenings
    ]
