"""Tags on a screening: film format, special events and previews.

Venues say these in different ways ("Presented in 70mm", "Filmmakers in
Attendance for Q&A", a "35mm" format field), so each tag is a pattern matched
against everything the venue told us about that one showing. A tag only
appears when the listing says so: a theatre that can run 70mm doesn't make
every screening there 70mm.
"""

import re

# How the film is shown.
FORMATS = {
    "70mm": r"\b70\s?mm\b",
    "35mm": r"\b35\s?mm\b",
    "16mm": r"\b16\s?mm\b",
    "IMAX": r"\bimax\b",
    # "4K restoration" describes the restoration, not the projection, so only
    # an explicit 4K presentation counts.
    "4K": r"\b(presented|projected|screening|screened|shown) in 4k\b",
    "Atmos": r"\bdolby atmos\b",
    "3D": r"\bin 3-?d\b|\b3-?d (screening|presentation)\b",
}

# What makes the showing more than a screening. Checked in this order.
# Names are kept short so they read at a glance on a small pill.
EVENTS = {
    "Q&A": r"\bq\s?&\s?a\b|\bq and a\b|talkback|in conversation",
    "Intro": r"introduc|\bintro\b",
    "Music": r"live (score|music|accompaniment|soundtrack)|accompanied by|\bperforms\b",
    "Festival": r"\bfestival\b|\bfest\b",
    "Shorts": r"\bshorts\b|short films",
    "Event": r"special (presentation|screening|event)",
    "Guests": r"\bguests?\b|in attendance|\bpanel\b",
}
# Advance, insider and sneak screenings are all one thing: a chance to see it early.
PREVIEWS = {
    "Preview": r"advance screening|insider screening|sneak preview|preview screening",
}

# The theatre's own description of a screening reads more freely, so only
# phrases that can't be about the film's story count there ("a festival" can be
# a plot point; "three short films" can't).
FROM_DESCRIPTION = {
    "Q&A": r"\bq\s?&\s?a\b|\bq and a\b|post-screening (talk|discussion)",
    "Music": r"live (score|music|musical accompaniment|accompaniment|soundtrack|performance)",
    "Shorts": r"\bshort films\b|\bshorts (program|programme|block|showcase)\b",
    "Intro": r"\bintroduced by\b|\bintroduction (by|from)\b",
}

# A tag the model gives is kept only if the listing has words that could back
# it up. Models misread: Llama tagged "35th Anniversary" as 35mm.
EVIDENCE = {
    "70mm": r"\b70\s?mm\b",
    "35mm": r"\b35\s?mm\b",
    "16mm": r"\b16\s?mm\b",
    "IMAX": r"\bimax\b",
    "4K": r"\b4k\b",
    "Atmos": r"\batmos\b",
    "3D": r"\b3-?d\b",
    "Q&A": r"q\s?&\s?a|q and a|question|talkback|conversation|discussion|\bchat|"
    r"with (the )?(director|filmmakers?)|in person",
    "Intro": r"\bintro",
    "Music": r"music|\bscore\b|\bband\b|concert|perform|orchestra|\bdj\b|\blive\b",
    "Festival": r"\bfest",
    "Shorts": r"\bshort",
    "Guests": r"in person|attend|guest|on.hand|\bjoin|panel|director|filmmaker|\bcast\b",
    "Preview": r"advance|preview|sneak|insider|early|premiere",
}


def backed_up(tag: str, text: str) -> bool:
    pattern = EVIDENCE.get(tag)
    return pattern is None or re.search(pattern, text, re.IGNORECASE) is not None


RARE = ["One screening"]
ORDER = RARE + list(FORMATS) + list(EVENTS) + list(PREVIEWS)
ON_FILM = {"70mm", "35mm", "16mm"}


def screening_tags(title: str, note: str | None, film_format: str | None) -> list[str]:
    text = " ".join(filter(None, [title, note, film_format])).lower()
    tags = [name for name, pattern in FORMATS.items() if re.search(pattern, text)]
    tags += [name for name, pattern in EVENTS.items() if re.search(pattern, text)]
    # A Q&A already means someone is there to answer.
    if "Q&A" in tags and "Guests" in tags:
        tags.remove("Guests")
    tags += [name for name, pattern in PREVIEWS.items() if re.search(pattern, text)]
    return tags


def description_tags(description: str | None) -> list[str]:
    """Tags a theatre's description makes plain (see FROM_DESCRIPTION)."""
    text = (description or "").lower()
    return [name for name, pattern in FROM_DESCRIPTION.items() if re.search(pattern, text)]


def said_by_tags(note: str | None, tags) -> bool:
    """True when a short note only says what a tag already shows ("Advance Screening"),
    so the pill can stand in for it."""
    if not note or len(note) > 40:
        return False
    words = re.sub(r"[^a-z0-9& ]", " ", note.lower()).split()
    covered = " ".join(t.lower() for t in tags)
    filler = {"presented", "in", "with", "a", "the", "screening", "screenings"}
    return bool(tags) and all(w in covered or w in filler for w in words)


def kind(tag: str) -> str:
    """Which family a tag belongs to, so each family can look different."""
    if tag in RARE:
        return "rare"
    if tag in FORMATS:
        return "format"
    return "event"


def is_event(tags) -> bool:
    return any(t in EVENTS for t in tags)


def sort_tags(tags) -> list[str]:
    return sorted(set(tags), key=lambda t: ORDER.index(t) if t in ORDER else len(ORDER))


def slug(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", tag.lower()).strip("-")


def filter_slugs(tags) -> list[str]:
    """Tag slugs for the filters, plus "on-film" and "event" for any film format or
    event, and "special" for either (the home page's Special screenings row)."""
    slugs = [slug(t) for t in tags if t != "Event"]
    if ON_FILM & set(tags):
        slugs.append("on-film")
    if is_event(tags):
        slugs.append("event")
    if "on-film" in slugs or "event" in slugs:
        slugs.append("special")
    return slugs
