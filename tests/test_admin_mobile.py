"""Drives the admin UI in a real browser at phone and desktop sizes.

Needs Playwright and a Chrome or Chromium; skipped otherwise. Install with
`pip install playwright` and either a system Chrome or `playwright install chromium`.
"""

from __future__ import annotations

import shutil
import socket
import threading
import time

import pytest

from egorovsystem import crypto
from egorovsystem.server import vault
from egorovsystem.server.app import create_app
from egorovsystem.server.config import Settings
from egorovsystem.server.db import Database

playwright_api = pytest.importorskip("playwright.sync_api")
uvicorn = pytest.importorskip("uvicorn")

from conftest import PASSWORD, USERNAME, AdminSession  # noqa: E402

ROUTES = ["overview", "variables", "functions", "devices", "audit", "settings"]
PHONE = {"viewport": {"width": 390, "height": 844}, "is_mobile": True, "has_touch": True}


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    settings = Settings(data_dir=tmp_path_factory.mktemp("mobile") / "data", secure_cookies=False)
    vault.initialize(Database(settings.db_path), USERNAME, PASSWORD, iterations=1000)
    app = create_app(settings)
    from starlette.testclient import TestClient

    with TestClient(app) as client:
        admin = AdminSession(client)
        admin.login()
        admin.create_variable("openai_keys", "sk-test", "a note long enough to need truncating on a narrow phone screen")
        admin.create_variable("a_rather_long_variable_name_that_must_not_widen_the_page", "x")
        admin.create_function("deploy", "cd ${openai_keys} && make", note="deploy")
        admin.create_device("laptop", scopes=["openai_*", "function:deploy"], expires_at=int(time.time()) + 86400)
    port = _free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}/admin/"
    server.should_exit = True
    thread.join(5)


@pytest.fixture(scope="module")
def browser():
    executable = shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chromium-browser")
    with playwright_api.sync_playwright() as pw:
        try:
            instance = pw.chromium.launch(executable_path=executable, headless=True)
        except Exception as error:  # no browser available
            pytest.skip(f"no Chrome or Chromium to drive: {error}")
        yield instance
        instance.close()


def _sign_in(context, url):
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.goto(url)
    page.fill("input[name=username]", USERNAME)
    page.fill("input[name=password]", PASSWORD)
    page.click("button[type=submit]")
    page.wait_for_selector("#page")
    return page, errors


STRAY_TEXT = """() => {
    const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
    const bad = [];
    while (walker.nextNode()) {
        const text = walker.currentNode.textContent.trim();
        if (["null", "undefined", "false", "NaN"].includes(text)) bad.push(text);
    }
    return bad;
}"""


def _assert_pages_clean(page, url):
    # Optional parts are written as `cond ? node : null`; none may leak as text.
    for route in ROUTES:
        page.goto(f"{url}#/{route}")
        page.wait_for_timeout(300)
        assert page.evaluate(STRAY_TEXT) == [], route


def test_phone_layout_fits_the_screen(live_server, browser):
    context = browser.new_context(**PHONE)
    page, errors = _sign_in(context, live_server)
    assert page.locator(".tabbar").count() == 1 and page.locator(".sidebar").count() == 0
    for route in ROUTES:
        page.goto(f"{live_server}#/{route}")
        page.wait_for_timeout(300)
        overflow = page.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        assert overflow <= 0, f"{route} is {overflow}px wider than the screen"
    _assert_pages_clean(page, live_server)
    assert errors == []
    context.close()


def test_phone_rows_open_sheets_and_locking_closes_them(live_server, browser):
    context = browser.new_context(**PHONE)
    page, errors = _sign_in(context, live_server)
    page.goto(f"{live_server}#/variables")
    page.locator(".m-row", has_text="openai_keys").locator(".m-row-open").click()
    sheet = page.locator("dialog.modal-sheet[open]")
    sheet.wait_for()
    sheet.get_by_role("button", name="显示").click()
    assert sheet.locator(".m-secret-text").inner_text() == "sk-test"
    # What the idle timer does; the lock button itself is behind the open sheet.
    page.evaluate("document.querySelector('.appbar-lock').click()")
    page.wait_for_selector("input[name=password]")
    assert page.locator("dialog").count() == 0
    assert errors == []
    context.close()


def test_desktop_keeps_the_sidebar(live_server, browser):
    context = browser.new_context(viewport={"width": 1280, "height": 860})
    page, errors = _sign_in(context, live_server)
    assert page.locator(".sidebar").count() == 1 and page.locator(".tabbar").count() == 0
    page.goto(f"{live_server}#/variables")
    page.wait_for_selector("table.catalog tbody tr")
    _assert_pages_clean(page, live_server)
    assert errors == []
    context.close()


NEWER = {"initialized": True, "authenticated": False, "version": "9.9.9", "ui": "ffffffffffff"}


def _pretend_newer_release(page):
    page.route("**/api/v1/admin/status", lambda route: route.fulfill(json=NEWER))


def test_a_newer_interface_reloads_a_locked_app_once(live_server, browser):
    context = browser.new_context(**PHONE)
    page = context.new_page()
    page.goto(live_server)
    page.wait_for_selector("input[name=username]")
    _pretend_newer_release(page)
    with page.expect_navigation():
        page.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector("input[name=username]")
    # Still told about the same release (old files keep coming back): no loop, a button instead.
    page.evaluate("window.notReloaded = true; document.dispatchEvent(new Event('visibilitychange'))")
    page.wait_for_selector(".update-bar")
    assert page.evaluate("window.notReloaded") is True
    context.close()


def test_a_newer_interface_waits_while_the_vault_is_in_use(live_server, browser):
    context = browser.new_context(**PHONE)
    page, errors = _sign_in(context, live_server)
    _pretend_newer_release(page)
    page.evaluate("window.notReloaded = true; window.dispatchEvent(new Event('online'))")
    page.wait_for_selector(".update-bar")
    assert page.evaluate("window.notReloaded") is True
    page.unroute("**/api/v1/admin/status")
    with page.expect_navigation():
        page.locator(".update-bar button").click()
    assert errors == []
    context.close()
