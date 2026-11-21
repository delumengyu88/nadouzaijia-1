import os
import socket
import subprocess
import sys
import threading
import time

import pytest

from nadou.admin.demo_data import DemoAccounts

pytestmark = pytest.mark.browser

PAGES = ("#/w01", "#/w02", "#/w03", "#/w04", "#/w05", "#/w06", "#/w08", "#/w09")
BANNED = (
    "待确认",
    "疑似命中",
    "确认命中",
    "别名",
    "匹配方式",
    "命中类型",
    "顺延",
    "VIP 结束后生效",
    "类别",
    "年份",
)
ENGLISH_LEAKS = ("user_id", "created_at", "undefined", "lorem", "TODO", "placeholder")


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
    server_log: list[str] = []

    def _drain() -> None:
        if server.stdout is None:
            return
        for line in server.stdout:
            server_log.append(line)

    threading.Thread(target=_drain, daemon=True).start()
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                if server.poll() is not None:
                    raise AssertionError("".join(server_log) or "server exited")
                time.sleep(0.2)
        else:
            raise AssertionError("workbench did not start\n" + "".join(server_log))
        errors: list[str] = []
        http_failures: list[str] = []
        with playwright_sync.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.on("pageerror", lambda exc: errors.append(str(exc)))
            page.on(
                "console",
                lambda msg: errors.append(msg.text) if msg.type == "error" else None,
            )
            page.on(
                "response",
                lambda res: (
                    http_failures.append(f"{res.status} {res.url}") if res.status >= 400 else None
                ),
            )
            page.on(
                "requestfailed",
                lambda req: http_failures.append(f"FAILED {req.url} {req.failure}"),
            )
            page.goto(f"{base}/admin/#/login", wait_until="networkidle")
            login_text = page.locator("body").inner_text()
            assert "账号登录" in login_text
            for word in BANNED:
                assert word not in login_text
            page.fill("input[name=username]", DemoAccounts.ADMIN)
            page.fill("input[name=password]", DemoAccounts.ADMIN_PASSWORD)
            page.click("button[type=submit]")
            try:
                page.locator("h1", has_text="取证申请").wait_for()
            except Exception as exc:
                body = page.locator("body").inner_text()
                raise AssertionError(
                    "取证申请 heading did not appear after login\n"
                    f"url={page.url}\n"
                    f"console={errors}\n"
                    f"http={http_failures}\n"
                    f"body={body[:2000]}\n"
                    f"server={''.join(server_log)[-4000:]}"
                ) from exc
            for route in PAGES:
                if route == "#/w01":
                    continue
                page.goto(f"{base}/admin/{route}", wait_until="networkidle")
                page.wait_for_function(
                    "() => {"
                    " const title = document.querySelector('h1');"
                    " return title && title.textContent.length > 0;"
                    "}"
                )
                text = page.locator("body").inner_text()
                assert len(text.strip()) > 40
                assert "没有载入" not in text
                assert "无法查看" not in text
                for word in BANNED:
                    assert word not in text
                for leak in ENGLISH_LEAKS:
                    assert leak not in text
            browser.close()
        assert errors == []
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
