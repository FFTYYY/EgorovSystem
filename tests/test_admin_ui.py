"""Static checks that keep the admin UI compatible with its strict CSP."""

import json
import re
import struct
from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "src" / "egorovsystem" / "server" / "static"


def asset_base(client):
    match = re.search(r'src="(assets/[0-9a-f]{12}/)js/main\.js"', client.get("/admin/").text)
    assert match, "the admin page must load main.js from the fingerprinted directory"
    return match.group(1)


def test_admin_page_and_assets_are_served(client):
    page = client.get("/admin/")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    base = asset_base(client)
    # data-cfasync="false" makes Cloudflare Rocket Loader leave the script alone,
    # so the zone-wide Rocket Loader setting does not matter.
    assert f'<script type="module" data-cfasync="false" src="{base}js/main.js"></script>' in page.text
    assert '="static/' not in page.text
    for path in ("app.css", "js/main.js", "js/crypto.js", "fonts/eb-garamond.woff2", "icons/favicon-32.png"):
        response = client.get(f"/admin/{base}{path}")
        assert response.status_code == 200, path
        assert response.headers["cache-control"] == "public, max-age=31536000, immutable", path
    assert client.get(f"/admin/{base}js/main.js").headers["content-type"].startswith("text/javascript")
    # The old unversioned paths still work but always revalidate.
    legacy = client.get("/admin/static/js/main.js")
    assert legacy.status_code == 200 and legacy.headers["cache-control"] == "no-cache"
    # Another release's directory is not served from this one.
    assert client.get("/admin/assets/000000000000/js/main.js").status_code == 404


def test_fingerprint_changes_with_any_file(tmp_path):
    from egorovsystem.server.assets import fingerprint

    (tmp_path / "js").mkdir()
    (tmp_path / "js" / "a.js").write_text("one")
    first = fingerprint(tmp_path)
    assert fingerprint(tmp_path) == first
    (tmp_path / "js" / "a.js").write_text("two")
    assert fingerprint(tmp_path) != first


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
    assert response.headers["cache-control"] == "no-store"
    # It precaches this release's fingerprinted files, under a cache of its own.
    assert f'const ASSETS = "{asset_base(client)}";' in response.text


def test_service_worker_stores_every_interface_file_and_nothing_else():
    source = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert 'const ASSETS = "static/";' in source  # the line the server rewrites
    files = source[source.index("const FILES = ["):]
    listed = set(re.findall(r'^\s+"([^"]+)",$', files[:files.index("];")], re.M))
    on_disk = {f"static/{p.relative_to(STATIC).as_posix()}" for p in STATIC.rglob("*")
               if (p.suffix in {".js", ".css", ".woff2", ".webmanifest", ".svg"} or p.name.startswith(("favicon-", "mascot-")))
               and p.name != "sw.js"}
    assert listed == on_disk
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


def test_status_reports_the_interface_fingerprint(client):
    # update.js compares this with the directory it was loaded from.
    assert f'assets/{client.get("/api/v1/admin/status").json()["ui"]}/' == asset_base(client)
