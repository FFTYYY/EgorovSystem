"""Static checks that keep the admin UI compatible with its strict CSP."""

import json
import re
import struct
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "egorovsystem" / "server" / "static"


def test_admin_page_and_assets_are_served(client):
    page = client.get("/admin/")
    assert page.status_code == 200
    # data-cfasync="false" makes Cloudflare Rocket Loader leave the script alone,
    # so the zone-wide Rocket Loader setting does not matter.
    assert '<script type="module" data-cfasync="false" src="static/js/main.js"></script>' in page.text
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


def test_phone_stylesheet_carries_the_layout_query():
    # layout.js reads the phone media query from this link, so it must exist exactly once.
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    links = re.findall(r'<link rel="stylesheet" href="static/mobile.css" media="([^"]+)" data-layout="mobile">', html)
    assert len(links) == 1 and "max-width" in links[0]


def test_manifest_makes_the_app_installable(client):
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert '<link rel="manifest" href="static/manifest.webmanifest" crossorigin="use-credentials">' in html
    assert '<link rel="apple-touch-icon" href="static/icons/apple-touch-icon.png">' in html
    response = client.get("/admin/static/manifest.webmanifest")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/manifest+json")
    manifest = json.loads(response.text)
    assert manifest["start_url"] == manifest["scope"] == "/admin/"
    assert manifest["display"] == "standalone"
    icons = {(icon["sizes"], icon["purpose"]): icon["src"] for icon in manifest["icons"]}
    for key in (("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")):
        assert key in icons, key
    for (sizes, _), src in icons.items():
        data = (STATIC / src).read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", src
        width, height = struct.unpack(">II", data[16:24])
        assert f"{width}x{height}" == sizes, src


def test_service_worker_controls_the_admin_page(client):
    response = client.get("/admin/sw.js")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/javascript")
    assert response.headers["cache-control"] == "no-cache"


def test_service_worker_stores_every_interface_file_and_nothing_else():
    source = (STATIC / "sw.js").read_text(encoding="utf-8")
    listed = set(re.findall(r'^\s+"([^"]+)",$', source[source.index("const SHELL"):source.index("];")], re.M))
    assert "./" in listed
    on_disk = {f"static/{p.relative_to(STATIC).as_posix()}" for p in STATIC.rglob("*")
               if p.suffix in {".js", ".css", ".woff2", ".webmanifest", ".svg"} and p.name != "sw.js"}
    assert listed - {"./"} == on_disk
    # The API is never answered from the cache.
    assert "startsWith(scope.pathname)" in source


def test_only_the_service_worker_policy_may_create_trusted_values(client):
    csp = client.get("/admin/").headers["content-security-policy"]
    assert "require-trusted-types-for 'script'" in csp
    assert re.search(r"trusted-types egorov-sw(;|$)", csp)
    creators = [str(p.relative_to(STATIC)) for p in STATIC.rglob("*.js") if "createPolicy(" in p.read_text(encoding="utf-8")]
    assert creators == ["js/pwa.js"]
    pwa = (STATIC / "js" / "pwa.js").read_text(encoding="utf-8")
    assert 'createPolicy("egorov-sw"' in pwa and "createHTML" not in pwa and "createScript:" not in pwa
