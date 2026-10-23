from datetime import UTC, datetime, timedelta

import pytest

from cineclock import ai
from cineclock.listings import tags_for
from cineclock.models import Screening
from cineclock.tags import backed_up

NOW = datetime(2026, 10, 18, 8, 0, tzinfo=UTC)


def test_answers_keep_to_our_tags_and_drop_junk():
    text = """[
      {"id": 0, "tags": ["Shorts", "Made up"], "festival": null},
      {"id": 1, "tags": ["Festival"], "festival": " European Union Film Festival 2026 "},
      {"id": 9, "tags": ["Q&A"]},
      "nonsense"
    ]"""
    assert ai.read_answer(text, 3) == [
        (["Shorts"], None),
        (["Festival"], "European Union Film Festival 2026"),
        ([], None),
    ]


def test_an_unreadable_answer_changes_nothing():
    assert ai.read_answer("sorry, I can't", 2) == [([], None), ([], None)]


def test_ai_tags_join_the_pattern_tags():
    s = Screening(
        "The Rio", "Night of Shorts", NOW, "https://x", ai_tags=("Shorts", "Music", "35mm"),
        synopsis="Five new films, with a band playing between them.",
    )  # fmt: skip
    # 35mm has nothing behind it in the listing, so it's dropped.
    assert tags_for(s) == ["Shorts", "Music"]


def test_each_film_at_a_theatre_is_read_once(tmp_path):
    from cineclock import db

    conn = db.connect(str(tmp_path / "c.db"))
    later = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)
    shows = [
        Screening("The Rio", "Jaws", later, "https://x/1"),
        Screening("The Rio", "Jaws", later.replace(hour=5), "https://x/2"),
    ]
    db.sync_venue(conn, "The Rio", shows, NOW)
    assert [s.title for s in ai.unread(conn, NOW)] == ["Jaws"]
    db.save_ai_note(conn, "The Rio", "Jaws", ["Q&A"], "Shark Week", NOW)
    assert ai.unread(conn, NOW) == []
    # Redoing reads every listing again, keeping the old notes until replaced.
    assert [s.title for s in ai.unread(conn, NOW, redo=True)] == ["Jaws"]
    assert db.load_ai_notes(conn)
    # A note over a week old is read again, so late-added guests get tagged.
    db.save_ai_note(conn, "The Rio", "Jaws", ["Q&A"], "Shark Week", NOW - timedelta(days=8))
    assert [s.title for s in ai.unread(conn, NOW)] == ["Jaws"]
    db.save_ai_note(conn, "The Rio", "Jaws", ["Q&A"], "Shark Week", NOW)
    [first, _] = db.upcoming(conn, NOW)
    assert first.ai_tags == ("Q&A",) and first.series == "Shark Week"


def test_listing_text_carries_what_the_theatre_said():
    s = Screening(
        "The Cinematheque", "Choosing Selfhood", NOW, "https://x", synopsis="Three short films."
    )
    assert (
        ai.listing_text(3, s)
        == "[3] Choosing Selfhood at The Cinematheque | description: Three short films."
    )


def test_json_part_finds_the_json_in_words():
    assert ai.json_part('Sure! [{"id": 0}] Hope that helps.') == '[{"id": 0}]'
    assert ai.json_part("no json here") == "no json here"


def test_cloudflare_tags_listings(monkeypatch):
    import httpx

    def handler(request):
        assert (
            request.url.path
            == "/client/v4/accounts/acc1/ai/run/@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        )
        assert request.headers["Authorization"] == "Bearer cf-token"
        reply = 'Here you go:\n```json\n[{"id": 0, "tags": ["Shorts"], "festival": null}]\n```'
        return httpx.Response(200, json={"result": {"response": reply}, "success": True})

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc1")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token")
    monkeypatch.setattr(ai, "CF_MODELS", ["@cf/meta/llama-3.3-70b-instruct-fp8-fast"])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ai.read_answer(ai.ask("tag these", client), 1) == [(["Shorts"], None)]


def test_a_retired_model_hands_over_to_the_next(monkeypatch):
    import httpx

    asked = []

    def handler(request):
        asked.append(request.url.path.rsplit("/", 1)[-1])
        if "70b" in request.url.path:
            return httpx.Response(400, json={"errors": [{"message": "No such model"}]})
        return httpx.Response(200, json={"result": {"response": "[]"}})

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc1")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "t")
    monkeypatch.setattr(
        ai,
        "CF_MODELS",
        ["@cf/meta/llama-3.3-70b-instruct-fp8-fast", "@cf/meta/llama-3.1-8b-instruct-fast"],
    )
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ai.ask("a", client)
        ai.ask("b", client)
    assert asked == [
        "llama-3.3-70b-instruct-fp8-fast",
        "llama-3.1-8b-instruct-fast",
        "llama-3.1-8b-instruct-fast",
    ]


def test_without_cloudflare_nothing_is_asked(monkeypatch):
    import httpx

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        with pytest.raises(RuntimeError):
            ai.ask("x", client)


def test_the_film_inside_an_event_title_is_read_and_checked():
    text = (
        '[{"id": 0, "tags": [], "festival": null, "film": "Just Eat It: A Food Waste Story",'
        ' "film_year": 2014}, {"id": 1, "film": "  ", "film_year": 1999},'
        ' {"id": 2, "film": "Wicker", "film_year": "soon"}]'
    )
    assert ai.read_films(text, 3) == [
        ("Just Eat It: A Food Waste Story", 2014),
        (None, None),
        ("Wicker", None),
    ]
    assert ai.read_films("nope", 1) == [(None, None)]


def test_a_tag_needs_words_in_the_listing_behind_it():
    freddy = "Freddy's Dead (35th Anniversary 3D Screening with Director Rachel Talalay!)"
    assert not backed_up("35mm", freddy)
    assert backed_up("3D", freddy)
    assert backed_up("Guests", freddy)
    assert backed_up("35mm", "Presented on 35 mm film")
    assert not backed_up("Shorts", "A feature-length documentary")
    assert backed_up("Event", "anything")  # no rule, so the model's word stands
