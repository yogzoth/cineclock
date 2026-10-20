"""A cheap AI read of new listings, for what the tag patterns miss.

    python -m cineclock.ai           tag listings not read yet (each deploy)
    python -m cineclock.ai --redo    read every upcoming listing again
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
import re
import sys
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx

from cineclock import db
from cineclock.scrapers import PatientClient
from cineclock.tags import EVENTS, FORMATS, PREVIEWS

API = "https://generativelanguage.googleapis.com/v1beta"
# Google retires model names over time, so unless one is set, use the newest
# Flash model the key can call (cheap and quick, plenty for tagging).
MODEL = os.environ.get("GEMINI_MODEL")
ALLOWED = [*FORMATS, *EVENTS, *PREVIEWS]
BATCH = 40
PER_RUN = 240
REREAD = timedelta(days=7)

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
4. "Check these": only problems you can see in the listings themselves:
   missing years or tags, duplicates, events listed as films. Never correct a
   year or title from memory; rep cinemas show obscure films that share a
   name with famous ones, and new films with old titles.
5. "Newsletter draft": a 150-word blurb Jordan could post as the monthly
   roundup, in his voice: casual, every sentence ending with a period.

Listings (title | theatre | dates | tags | series | new):
{listings}"""


def rank_models(names: list[str]) -> list[str]:
    """Plain Flash models, newest first: no lite, preview, image, audio or live variants."""
    skip = ("lite", "preview", "exp", "image", "tts", "audio", "live", "thinking", "8b")
    flash = [
        n.removeprefix("models/") for n in names if "flash" in n and not any(s in n for s in skip)
    ]

    def version(name: str) -> tuple:
        return tuple(int(x) for x in re.findall(r"\d+", name))

    return sorted(flash, key=version, reverse=True)


def pick_model(names: list[str]) -> str | None:
    ranked = rank_models(names)
    return ranked[0] if ranked else None


# Models to try, best first. A model that stays overloaded or has been retired
# drops out for the rest of the run and the next one is used.
MODELS: list[str] = [MODEL] if MODEL else []
GIVE_UP = {404, 429, 500, 503}


def models(key: str, client: httpx.Client) -> list[str]:
    if not MODELS:
        response = client.get(
            f"{API}/models", headers={"x-goog-api-key": key}, params={"pageSize": 200}
        )
        response.raise_for_status()
        usable = [
            m["name"]
            for m in response.json().get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        MODELS.extend(rank_models(usable)[:3] + ["gemini-flash-latest"])
        print(f"::notice::Using {MODELS[0]}")
    return MODELS


def ask_gemini(
    prompt: str, key: str, client: httpx.Client, as_json: bool = True, wait: int = 120
) -> str:
    config = {"temperature": 0.2}
    if as_json:
        config["responseMimeType"] = "application/json"
    candidates = models(key, client)
    while True:
        response = client.post(
            f"{API}/models/{candidates[0]}:generateContent",
            headers={"x-goog-api-key": key},
            json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": config},
            timeout=wait,
        )
        if response.status_code in GIVE_UP and len(candidates) > 1:
            dropped = candidates.pop(0)
            print(f"::notice::{dropped} answered {response.status_code}; using {candidates[0]}")
            continue
        response.raise_for_status()
        parts = response.json()["candidates"][0]["content"]["parts"]
        return "".join(p.get("text", "") for p in parts)


# Cloudflare Workers AI: the backup, on the free daily allowance of Jordan's
# Cloudflare account. Needs CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN.
CF_API = "https://api.cloudflare.com/client/v4/accounts/{account}/ai/run/{model}"
CF_MODEL = os.environ.get("CF_AI_MODEL", "@cf/meta/llama-3.3-70b-instruct-fp8-fast")


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
    response = client.post(
        CF_API.format(account=account, model=CF_MODEL),
        headers={"Authorization": f"Bearer {token}"},
        json={
            "messages": [
                {"role": "system", "content": "Answer with JSON only." if as_json else "Be brief."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": 4096,
            "temperature": 0.2,
        },
        timeout=wait,
    )
    response.raise_for_status()
    answer = response.json()["result"]["response"]
    if not isinstance(answer, str):  # some models hand back parsed JSON
        return json.dumps(answer)
    return json_part(answer) if as_json else answer


# Set once Gemini has failed in this run, so later batches go straight to the backup.
GEMINI_DOWN = False


def ask(
    prompt: str, key: str | None, client: httpx.Client, as_json: bool = True, wait: int = 120
) -> str:
    """Gemini first; Cloudflare when there's no Gemini key or Gemini won't answer."""
    global GEMINI_DOWN
    if key and not GEMINI_DOWN:
        try:
            return ask_gemini(prompt, key, client, as_json, wait)
        except httpx.HTTPError as error:
            if not cloudflare():
                raise
            GEMINI_DOWN = True
            print(f"::notice::Gemini unavailable ({error}{google_says(error)}); using Cloudflare")
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


def tag_new(conn, key: str, client: httpx.Client, now: datetime, redo: bool = False) -> int:
    todo = unread(conn, now, redo)[: PER_RUN * (3 if redo else 1)]
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
    # The whole programme in one go, so give it longer than a tagging batch.
    prompt = REPORT_PROMPT.format(listings="\n".join(lines))
    return ask(prompt, key, client, as_json=False, wait=600)


def google_says(error: Exception) -> str:
    """Google's own reason for refusing ("quota exceeded", "API not enabled")."""
    response = getattr(error, "response", None)
    try:
        message = response.json()["error"]["message"] if response is not None else ""
    except (ValueError, KeyError, TypeError):
        return ""
    return f" ({message[:200]})" if message else ""


def main() -> int:
    key = os.environ.get("GEMINI_API_KEY")
    if not key and not cloudflare():
        print("No GEMINI_API_KEY or Cloudflare token; skipping the AI read.")
        return 0
    conn = db.connect()
    now = datetime.now(UTC)
    # Asks again, after a pause, when a service is busy or briefly down (429, 503).
    with PatientClient(timeout=120) as client:
        try:
            if "--report" in sys.argv:
                with open("report.md", "w") as f:
                    f.write(report(conn, key, client, now))
                print("Wrote report.md")
            else:
                redo = "--redo" in sys.argv
                done = tag_new(conn, key, client, now, redo)
                print(f"::notice::AI read {done} {'listings again' if redo else 'new listings'}.")
        except (httpx.HTTPError, KeyError, IndexError, RuntimeError) as error:
            # Never stop a deploy over this; the pattern tags still stand.
            print(f"::warning::AI read skipped: {error}{google_says(error)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
