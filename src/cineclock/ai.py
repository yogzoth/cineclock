"""A cheap AI read of new listings, for what the tag patterns miss.

    python -m cineclock.ai           tag listings not read yet (each deploy)
    python -m cineclock.ai --report  write this week's report (report.md)

Patterns in tags.py catch "35mm" or "Q&A" when a theatre says it plainly, but
listings word things every way ("three of her short films", "with a live band",
a festival named only in a paragraph). Each new film at a theatre gets read
once by Gemini, which may only answer with tags from our own list plus the
festival's name, and that's stored (db.ai_notes) so it's never asked twice.

Needs GEMINI_API_KEY. Without it, or if Gemini fails, nothing changes: the site
just keeps the pattern tags.
"""

import json
import os
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx

from cineclock import db
from cineclock.tags import EVENTS, FORMATS, PREVIEWS

MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
URL = f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent"
ALLOWED = [*FORMATS, *EVENTS, *PREVIEWS]
BATCH = 40
PER_RUN = 240

TAG_PROMPT = """You tag cinema listings for a Vancouver showtimes site.
For each listing, return only tags the listing itself clearly states, chosen
from this list and spelled exactly so: {allowed}.
- "Shorts": a programme of short films. "Music": live music or a live score.
- "Q&A", "Intro", "Guests": someone appears in person.
- "Festival": part of a named film festival. Put the festival's full name in
  "festival" (for example "European Union Film Festival 2026"), else null.
Never guess from the film's plot or reputation. When unsure, leave it out.
Answer with a JSON list, one object per listing, in order:
[{{"id": 0, "tags": [], "festival": null}}]

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
4. "Check these": listings whose tags or details look wrong or missing, and
   anything odd (duplicates, missing years, events listed as films).
5. "Newsletter draft": a 150-word blurb Jordan could post as the monthly
   roundup, in his voice: casual, every sentence ending with a period.

Listings (title | theatre | dates | tags | series | new):
{listings}"""


def ask(prompt: str, key: str, client: httpx.Client, as_json: bool = True) -> str:
    config = {"temperature": 0.2}
    if as_json:
        config["responseMimeType"] = "application/json"
    response = client.post(
        URL,
        headers={"x-goog-api-key": key},
        json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config},
        timeout=120,
    )
    response.raise_for_status()
    parts = response.json()["candidates"][0]["content"]["parts"]
    return "".join(p.get("text", "") for p in parts)


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


def unread(conn, now: datetime) -> list:
    """One upcoming listing per film per theatre that hasn't been read yet."""
    known = db.load_ai_notes(conn)
    seen, todo = set(), []
    for s in db.upcoming(conn, now):
        key = (s.venue, s.title)
        if key in known or key in seen:
            continue
        seen.add(key)
        todo.append(s)
    return todo


def tag_new(conn, key: str, client: httpx.Client, now: datetime) -> int:
    todo = unread(conn, now)[:PER_RUN]
    done = 0
    for start in range(0, len(todo), BATCH):
        batch = todo[start : start + BATCH]
        prompt = TAG_PROMPT.format(
            allowed=", ".join(ALLOWED),
            listings="\n".join(listing_text(i, s) for i, s in enumerate(batch)),
        )
        answers = read_answer(ask(prompt, key, client), len(batch))
        for s, (tags, festival) in zip(batch, answers, strict=True):
            db.save_ai_note(conn, s.venue, s.title, tags, festival, now)
        conn.commit()
        done += len(batch)
    return done


def report(conn, key: str, client: httpx.Client, now: datetime) -> str:
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
    return ask(REPORT_PROMPT.format(listings="\n".join(lines)), key, client, as_json=False)


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        print("No GEMINI_API_KEY; skipping the AI read.")
        return 0
    conn = db.connect()
    now = datetime.now(UTC)
    with httpx.Client() as client:
        try:
            if "--report" in sys.argv:
                with open("report.md", "w") as f:
                    f.write(report(conn, key, client, now))
                print("Wrote report.md")
            else:
                print(f"AI read {tag_new(conn, key, client, now)} new listings.")
        except (httpx.HTTPError, KeyError, IndexError) as error:
            # Never stop a deploy over this; the pattern tags still stand.
            print(f"::warning::AI read skipped: {error}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
