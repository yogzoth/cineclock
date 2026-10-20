from datetime import UTC, datetime, timedelta

import pytest

from cineclock import ai
from cineclock.listings import tags_for
from cineclock.models import Screening

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
    s = Screening("The Rio", "Night of Shorts", NOW, "https://x", ai_tags=("Shorts", "Music"))
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


def test_picks_the_newest_plain_flash_model():
    names = [
        "models/gemini-2.0-flash",
        "models/gemini-2.5-flash",
        "models/gemini-2.5-flash-lite",
        "models/gemini-3.0-flash-preview",
        "models/gemini-2.5-pro",
        "models/gemini-2.5-flash-image",
    ]
    assert ai.pick_model(names) == "gemini-2.5-flash"
    assert ai.pick_model(["models/gemini-2.5-pro"]) is None


def test_a_busy_or_retired_model_hands_over_to_the_next(monkeypatch):
    import httpx

    asked = []

    def handler(request):
        if request.url.path.endswith("/models"):
            names = ["models/gemini-3.8-flash", "models/gemini-3.5-flash", "models/gemini-2.5-pro"]
            return httpx.Response(
                200,
                json={
                    "models": [
                        {"name": n, "supportedGenerationMethods": ["generateContent"]}
                        for n in names
                    ]
                },
            )
        asked.append(request.url.path.split("/")[-1])
        if "3.8" in request.url.path:
            return httpx.Response(503)
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "[]"}]}}]})

    monkeypatch.setattr(ai, "MODELS", [])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert ai.ask("hi", "key", client) == "[]"
        assert ai.ask("again", "key", client) == "[]"
    assert asked == [
        "gemini-3.8-flash:generateContent",
        "gemini-3.5-flash:generateContent",
        "gemini-3.5-flash:generateContent",
    ]


def test_cloudflare_answers_when_gemini_wont(monkeypatch):
    import httpx

    def handler(request):
        if "googleapis" in request.url.host:
            return httpx.Response(503)
        assert (
            request.url.path
            == "/client/v4/accounts/acc1/ai/run/@cf/meta/llama-3.3-70b-instruct-fp8-fast"
        )
        assert request.headers["Authorization"] == "Bearer cf-token"
        reply = 'Here you go:\n```json\n[{"id": 0, "tags": ["Shorts"], "festival": null}]\n```'
        return httpx.Response(200, json={"result": {"response": reply}, "success": True})

    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc1")
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-token")
    monkeypatch.setattr(ai, "MODELS", ["gemini-3.8-flash"])
    monkeypatch.setattr(ai, "GEMINI_DOWN", False)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        answer = ai.ask("tag these", "gemini-key", client)
    assert ai.read_answer(answer, 1) == [(["Shorts"], None)]
    assert ai.GEMINI_DOWN  # later batches skip straight to Cloudflare


def test_without_any_ai_configured_nothing_is_asked(monkeypatch):
    import httpx

    monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
    monkeypatch.setattr(ai, "GEMINI_DOWN", False)
    with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(500))) as client:
        with pytest.raises(RuntimeError):
            ai.ask("x", None, client)


def test_json_part_finds_the_json_in_words():
    assert ai.json_part('Sure! [{"id": 0}] Hope that helps.') == '[{"id": 0}]'
    assert ai.json_part("no json here") == "no json here"
