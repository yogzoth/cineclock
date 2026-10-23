"""A cheap AI read of new listings, for what the tag patterns miss.

    python -m cineclock.ai           tag listings not read yet (each deploy)
    python -m cineclock.ai --redo    read every upcoming listing again
    python -m cineclock.ai --report  write this week's report (report.md)

Patterns in tags.py catch "35mm" or "Q&A" when a theatre says it plainly, but
listings word things every way ("three of her short films", "with a live band",
a festival named only in a paragraph). Each new film at a theatre gets read
by an AI model, which may only answer with tags from our own list plus the
festival's name. That's stored (db.ai_notes) and read again weekly.

The model is Llama on Cloudflare Workers AI, on the free daily allowance of
Jordan's Cloudflare account (Gemini's free tier turned most calls away). Needs
CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN. Without them, or if Cloudflare
fails, nothing changes: the site just keeps the pattern tags.
"""

import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx

from cineclock import db
from cineclock.scrapers import PatientClient
from cineclock.tags import EVENTS, FORMATS, PREVIEWS, backed_up

ALLOWED = [*FORMATS, *EVENTS, *PREVIEWS]
BATCH = 40
PER_RUN = 240
REREAD = timedelta(days=7)


TAG_PROMPT = """You tag cinema listings for a Vancouver showtimes site.
For each listing, return only tags the listing itself clearly states, chosen
from this list and spelled exactly so: {allowed}.
- "Shorts": a programme of short films. "Music": live music or a live score.
- "Q&A", "Intro", "Guests": someone appears in person.
- "Festival": part of a named film festival, including a film fest or
  competition whose screenings are listed separately ("Shits n Giggles 2026:
  Opening Night", "the best of the fest"). Put its full name in "festival"
  (for example "European Union Film Festival 2026"), else null.
- "35mm", "70mm" and other formats only when the listing names the format;
  "35th anniversary" is not 35mm.
- "film": when the listing's title wraps a feature film in an event name
  ("Food Stash Foundation 10 Year Anniversary: Just Eat It - A Food Waste
  Story", "Ken Russell's 'The Devils'"), the film's own title as Letterboxd
  would have it ("Just Eat It: A Food Waste Story"), and "film_year" if the
  listing gives it. Null when the title is already just the film, or when
  it's a programme of several films or a live show.
Never guess from the film's plot or reputation. When unsure, leave it out.
Answer with a JSON list, one object per listing, in order:
[{{"id": 0, "tags": [], "festival": null, "film": null, "film_year": null}}]

Listings:
{listings}"""

REPORT_PROMPT = """You help Jordan run cineclock, a showtimes site for
Vancouver's independent cinemas (the Rio, the Park, the Cinematheque, VIFF
Centre and the Dunbar). Here is everything listed for the next five weeks,
and which listings are new since last week.

Write a short weekly note in Markdown, friendly and plain, no em dashes:
1. "This week": three to five highlights worth telling a friend about.
2. "Festivals and series": any running or coming up, with dates.
3. "New this week": what was added, in a sentence or two.
4. "Check these": only problems you can see in the listings themselves:
   missing years or tags, duplicates, events listed as films. Never correct a
   year or title from memory; rep cinemas show obscure films that share a
   name with famous ones, and new films with old titles.
5. "Newsletter draft": a 150-word blurb Jordan could post as the monthly
   roundup, in his voice: casual, every sentence ending with a period.

Listings (title | theatre | dates | tags | series | new):
{listings}"""


# Cloudflare Workers AI models, best first. If one is retired or won't answer,
# the next is used for the rest of the run. CF_AI_MODEL puts another in front.
CF_API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
CF_MODELS = [
    m
    for m in (
        os.environ.get("CF_AI_MODEL"),
        "@cf/meta/llama-3.3-70b-instruct-fp8-fast",
        "@cf/meta/llama-3.1-8b-instruct-fast",
    )
    if m
]
GIVE_UP = {400, 404, 429, 500, 502, 503, 504}


def cloudflare() -> tuple[str, str] | None:
    account = os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    token = os.environ.get("CLOUDFLARE_API_TOKEN")
    return (account, token) if account and token else None


def json_part(text: str) -> str:
    """The JSON in an answer that wrapped it in words or a code fence."""
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    end = max(text.rfind("]"), text.rfind("}"))
    return text[min(starts) : end + 1] if starts and end > min(starts) else text


def ask_cloudflare(prompt: str, client: httpx.Client, as_json: bool = True, wait: int = 120) -> str:
    account, token = cloudflare()
    while True:
        response = client.post(
            CF_API.format(account=account, model=CF_MODELS[0]),
            headers={"Authorization": f"Bearer {token}"},
            json={
                "messages": [
                    {
                        "role": "system",
                        "content": "Answer with JSON only." if as_json else "Be brief.",
                    },
                    {"role": "user", "content": prompt},
                ],
                "max_tokens": 4096,
                "temperature": 0.2,
            },
            timeout=wait,
        )
        if response.status_code in GIVE_UP and len(CF_MODELS) > 1:
            dropped = CF_MODELS.pop(0)
            print(f"::notice::{dropped} answered {response.status_code}; using {CF_MODELS[0]}")
            continue
        response.raise_for_status()
        answer = response.json()["result"]["response"]
        if not isinstance(answer, str):  # some models hand back parsed JSON
            return json.dumps(answer)
        return json_part(answer) if as_json else answer


def ask(prompt: str, client: httpx.Client, as_json: bool = True, wait: int = 120) -> str:
    if not cloudflare():
        raise RuntimeError("no AI configured")
    return ask_cloudflare(prompt, client, as_json, wait)


def listing_text(i: int, s) -> str:
    bits = [f"[{i}] {s.title} at {s.venue}"]
    for label, value in (
        ("note", s.note),
        ("series", s.series),
        ("format", s.film_format),
        ("description", s.synopsis),
    ):
        if value:
            bits.append(f"{label}: {value[:600]}")
    return " | ".join(bits)


def read_answer(text: str, count: int) -> list[tuple[list[str], str | None]]:
    """The model's answer, kept to our tag list. Anything malformed reads as nothing."""
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        return [([], None)] * count
    found: list[tuple[list[str], str | None]] = [([], None)] * count
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        if not 0 <= item["id"] < count:
            continue
        tags = [t for t in item.get("tags") or [] if t in ALLOWED]
        festival = item.get("festival")
        festival = festival.strip()[:80] if isinstance(festival, str) and festival.strip() else None
        found[item["id"]] = (sorted(set(tags), key=ALLOWED.index), festival)
    return found


def unread(conn, now: datetime, redo: bool = False) -> list:
    """One upcoming listing per film per theatre that hasn't been read, or was
    last read over a week ago (theatres add guests and details as a date nears),
    or every one with `redo`. Old notes stand until each is replaced. Never-read
    listings come first."""
    checked = {} if redo else db.ai_notes_checked(conn)
    fresh_after = now - REREAD
    seen, new, stale = set(), [], []
    for s in db.upcoming(conn, now):
        key = (s.venue, s.title)
        if key in seen or (key in checked and checked[key] > fresh_after):
            continue
        seen.add(key)
        (stale if key in checked else new).append(s)
    return new + stale


def read_films(text: str, count: int) -> list[tuple[str | None, int | None]]:
    """The real film inside each event-style title, when the model found one."""
    found: list[tuple[str | None, int | None]] = [(None, None)] * count
    try:
        items = json.loads(text)
    except json.JSONDecodeError:
        return found
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), int):
            continue
        if not 0 <= item["id"] < count:
            continue
        film = item.get("film")
        year = item.get("film_year")
        film = film.strip()[:120] if isinstance(film, str) and film.strip() else None
        year = year if isinstance(year, int) and 1880 <= year <= 2100 else None
        found[item["id"]] = (film, year if film else None)
    return found


def tag_new(conn, client: httpx.Client, now: datetime, redo: bool = False) -> int:
    todo = unread(conn, now, redo)[: PER_RUN * (3 if redo else 1)]
    done = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        prompt = TAG_PROMPT.format(
            allowed=", ".join(ALLOWED),
            listings="\n".join(listing_text(i, s) for i, s in enumerate(batch)),
        )
        text = ask(prompt, client)
        answers = zip(read_answer(text, len(batch)), read_films(text, len(batch)), strict=True)
        for s, ((tags, festival), (film, year)) in zip(batch, answers, strict=True):
            said = " ".join(filter(None, [s.title, s.note, s.series, s.film_format, s.synopsis]))
            tags = [t for t in tags if backed_up(t, said)]
            db.save_ai_note(conn, s.venue, s.title, tags, festival, now, film, year)
        conn.commit()
        done += len(batch)
    return done


def report(conn, client: httpx.Client, now: datetime) -> str:
    from cineclock.listings import film_pages

    pages = {id(p): p for p in film_pages(db.upcoming(conn, now), db.films_by_key(conn)).values()}
    soon = now + timedelta(days=35)
    week_ago = now - timedelta(days=7)
    lines = []
    for p in sorted(pages.values(), key=lambda p: p.next_showing.starts_at):
        if p.next_showing.starts_at > soon:
            continue
        new = any(s.first_seen and s.first_seen > week_ago for s in p.screenings)
        venues = ", ".join(sorted({s.venue for s in p.screenings}))
        days = Counter(s.local_start.strftime("%b %d") for s in p.screenings)
        lines.append(
            f"{p.title} ({p.year or '?'}) | {venues} | {', '.join(days)} | "
            f"{', '.join(p.tags) or '-'} | {p.series or '-'} | {'NEW' if new else ''}"
        )
    # The whole programme in one go, so give it longer than a tagging batch.
    prompt = REPORT_PROMPT.format(listings="\n".join(lines))
    return ask(prompt, client, as_json=False, wait=600)


def service_says(error: Exception) -> str:
    """Cloudflare's own reason for refusing ("quota exceeded", "bad token")."""
    response = getattr(error, "response", None)
    try:
        errors = response.json().get("errors") if response is not None else None
        message = errors[0]["message"] if errors else ""
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        return ""
    return f" ({str(message)[:200]})" if message else ""


def main() -> int:
    if not cloudflare():
        print("No Cloudflare token; skipping the AI read.")
        return 0
    conn = db.connect()
    now = datetime.now(UTC)
    # Asks again, after a pause, when a service is busy or briefly down (429, 503).
    with PatientClient(timeout=120) as client:
        try:
            if "--report" in sys.argv:
                with open("report.md", "w") as f:
                    f.write(report(conn, client, now))
                print("Wrote report.md")
            else:
                redo = "--redo" in sys.argv
                done = tag_new(conn, client, now, redo)
                print(f"::notice::AI read {done} {'listings again' if redo else 'new listings'}.")
        except (httpx.HTTPError, KeyError, IndexError, RuntimeError) as error:
            # Never stop a deploy over this; the pattern tags still stand.
            print(f"::warning::AI read skipped: {error}{service_says(error)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
