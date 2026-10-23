import json
from datetime import UTC, datetime, timedelta

import pytest

from cineclock import db
from cineclock.build import build
from cineclock.models import Screening

NOW = datetime(2026, 10, 18, 8, 45, tzinfo=UTC)


@pytest.fixture
def conn():
    connection = db.connect(":memory:")
    yield connection
    connection.close()


def test_build_writes_every_page_and_asset(conn, tmp_path):
    db.sync_venue(
        conn, "The Rio", [Screening("The Rio", "Scream", NOW + timedelta(days=1), "https://x")], NOW
    )
    out = tmp_path / "site"
    count = build(conn, out, NOW)

    # home, showtimes, films, theatres, about, privacy, one film, 5 venues, films.json,
    # alerts.json, Letterboxd page, then the 404 page, the sitemap and robots.txt
    assert count == 1 + 1 + 1 + 1 + 1 + 1 + 1 + 5 + 1 + 1 + 1 + 1 + 3
    assert "Scream" in (out / "index.html").read_text()
    assert "Scream" in (out / "showtimes/index.html").read_text()
    assert (out / "films/scream/index.html").exists()
    assert (out / "theatres/rio/index.html").exists()
    assert (out / "static/site.css").exists()
    assert (out / "static/fonts/source-serif-4.woff2").exists()
    assert "Updated Sun Oct 18, 1:45 am" in (out / "index.html").read_text()


def test_build_publishes_stats(conn, tmp_path):
    build(conn, tmp_path / "site", NOW)
    summary = json.loads((tmp_path / "site/stats.json").read_text())
    assert summary["screenings"] == 0
    assert summary["updated"].startswith("2026-10-18T08:45")


def test_build_replaces_old_output(conn, tmp_path):
    out = tmp_path / "site"
    (out / "films/gone").mkdir(parents=True)
    (out / "films/gone/index.html").write_text("old")
    build(conn, out, NOW)
    assert not (out / "films/gone").exists()


def test_no_page_links_outside_the_site_root(conn, tmp_path):
    # GitHub Pages serves from /cineclock/, so links starting with "/" would break.
    db.sync_venue(
        conn, "The Rio", [Screening("The Rio", "Scream", NOW + timedelta(days=1), "https://x")], NOW
    )
    out = tmp_path / "site"
    build(conn, out, NOW)
    for page in out.rglob("*.html"):
        html = page.read_text()
        assert 'href="/' not in html and 'src="/' not in html, page


def test_stylesheet_link_changes_when_the_stylesheet_does(conn, tmp_path, monkeypatch):
    import cineclock.render as render

    build(conn, tmp_path / "a", NOW)
    first = (tmp_path / "a/index.html").read_text()
    monkeypatch.setitem(render.env.globals, "asset_version", lambda name: "changed123")
    build(conn, tmp_path / "b", NOW)
    second = (tmp_path / "b/index.html").read_text()
    assert "static/site.css?v=" in first
    assert "static/site.css?v=changed123" in second and first != second


def test_build_adds_a_404_page_and_sitemap(conn, tmp_path):
    db.sync_venue(
        conn, "The Rio", [Screening("The Rio", "Scream", NOW + timedelta(days=1), "https://x")], NOW
    )
    out = tmp_path / "site"
    build(conn, out, NOW)
    missing = (out / "404.html").read_text()
    assert "This one isn't playing." in missing
    assert 'href="https://cineclock.ca/films/"' in missing
    sitemap = (out / "sitemap.xml").read_text()
    assert "<loc>https://cineclock.ca/</loc>" in sitemap
    assert "<loc>https://cineclock.ca/films/scream/</loc>" in sitemap
    robots = (out / "robots.txt").read_text()
    assert "Sitemap: https://cineclock.ca/sitemap.xml" in robots
    home = (out / "index.html").read_text()
    assert '<link rel="canonical" href="https://cineclock.ca/">' in home


def test_every_local_link_and_asset_resolves(conn, tmp_path):
    """Each page's own links, scripts, stylesheets and images point at a file
    the build wrote. A macro that couldn't see `root` once left Showtimes
    loading static/calendar.js from the wrong folder."""
    import re
    from urllib.parse import urljoin, urlparse

    db.sync_venue(
        conn,
        "The Rio",
        [Screening("The Rio", "Scream", NOW + timedelta(days=d), f"https://x/{d}") for d in (1, 2)],
        NOW,
    )
    out = tmp_path / "site"
    build(conn, out, NOW)
    missing = []
    for page in out.rglob("*.html"):
        if page.name == "404.html":
            continue  # links from the site's full address, for any depth
        base = "https://site/" + str(page.relative_to(out))
        for ref in re.findall(r'(?:href|src)="([^"#]+)', page.read_text()):
            url = urlparse(urljoin(base, ref))
            if url.netloc != "site" or ref.startswith("mailto:") or "${" in ref:
                continue
            path = url.path.lstrip("/")
            target = out / path
            if path.endswith("/") or not path:
                target = target / "index.html"
            if not target.exists():
                missing.append(f"{page.relative_to(out)} -> {ref}")
    assert not missing, missing[:10]
