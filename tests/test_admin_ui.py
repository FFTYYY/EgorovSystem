"""Static checks that keep the admin UI compatible with its strict CSP."""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "egorovsystem" / "server" / "static"


def test_admin_page_and_assets_are_served(client):
    page = client.get("/admin/")
    assert page.status_code == 200
    assert '<script type="module" src="static/js/main.js"></script>' in page.text
    for path in ("static/app.css", "static/js/main.js", "static/js/crypto.js", "static/fonts/eb-garamond.woff2",
                 "static/favicon.svg"):
        response = client.get(f"/admin/{path}")
        assert response.status_code == 200, path
    assert client.get("/admin/static/js/main.js").headers["content-type"].startswith("text/javascript")


def test_no_inline_script_or_style():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), "inline <script> would be blocked by the CSP"
    assert "style=" not in html and "<style" not in html


def test_ui_never_turns_strings_into_html():
    # Trusted Types is enforced with no policy, so these sinks would throw.
    sinks = re.compile(r"\.(innerHTML|outerHTML)\s*=|insertAdjacentHTML|document\.write|\beval\(|new Function\(")
    offenders = [str(p.relative_to(STATIC)) for p in STATIC.rglob("*.js") if sinks.search(p.read_text(encoding="utf-8"))]
    assert offenders == []


def test_bundled_fonts_ship_with_their_licenses():
    fonts = STATIC / "fonts"
    for family in ("ebgaramond", "imfellenglish", "jetbrainsmono"):
        assert "SIL OPEN FONT LICENSE" in (fonts / f"OFL-{family}.txt").read_text(encoding="utf-8")
