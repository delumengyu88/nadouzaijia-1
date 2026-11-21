import os
import socket
import subprocess
import sys
import time

import pytest

from nadou.admin.demo_data import DemoAccounts

pytestmark = pytest.mark.browser

PAGES = ("#/w02", "#/w03", "#/w04", "#/w05", "#/w06", "#/w08", "#/w09")


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_each_workbench_page_loads_without_console_errors():
    if os.environ.get("NADOU_RUN_BROWSER") != "1":
        pytest.skip("set NADOU_RUN_BROWSER=1 to load the workbench in Chromium")
    database = os.environ.get("DATABASE_URL", "")
    if not database.startswith("postgresql"):
        pytest.skip("browser check uses the Postgres started for CI")
    playwright_sync = pytest.importorskip("playwright.sync_api")
    from nadou.__main__ import DemoCommand

    assert DemoCommand.run() == 0
    port = _free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "nadou.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
        env=os.environ.copy(),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if server.poll() is not None:
                    raise AssertionError(server.stdout.read() if server.stdout else "server exited")
                time.sleep(0.2)
        else:
            raise AssertionError("workbench did not start")
        errors: list[str] = []
        with playwright_sync.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on(
                "console",
                lambda msg: errors.append(msg.text) if msg.type == "error" else None,
            )
            page.goto(f"{base}/admin/#/login", wait_until="networkidle")
            page.fill("input[name=username]", DemoAccounts.ADMIN)
            page.fill("input[name=password]", DemoAccounts.ADMIN_PASSWORD)
            page.click("button[type=submit]")
            page.wait_for_selector("text=取证申请")
            for route in PAGES:
                page.goto(f"{base}/admin/{route}", wait_until="networkidle")
                text = page.locator("body").inner_text()
                assert len(text.strip()) > 40
                assert "没有载入" not in text
                assert "无法查看" not in text
            browser.close()
        assert errors == []
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
