from datetime import UTC, datetime

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
