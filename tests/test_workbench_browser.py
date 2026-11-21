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


def test_support_does_not_inherit_admin_user_details():
    """Admin bans a user, logs out, and 客服 must see only the ban and a masked phone."""
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
    phone = "13700009054"
    openid = "o0000000000000a5"
    server_log: list[str] = []

    def _drain() -> None:
        if server.stdout is None:
            return
        for line in server.stdout:
            server_log.append(line)

    threading.Thread(target=_drain, daemon=True).start()

    def sign_in(page, username: str, password: str) -> None:
        page.locator("h2", has_text="账号登录").wait_for()
        page.fill("input[name=username]", username)
        page.fill("input[name=password]", password)
        with page.expect_response(
            lambda res: res.url.endswith("/auth/login") and res.request.method == "POST"
        ) as login:
            page.click("button[type=submit]")
        detail = f"{login.value.status} {login.value.text()} {''.join(server_log)[-2000:]}"
        assert login.value.ok, detail
        page.locator("h1", has_text="取证申请").wait_for()

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
        with playwright_sync.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{base}/admin/#/login", wait_until="networkidle")
            sign_in(page, DemoAccounts.ADMIN, DemoAccounts.ADMIN_PASSWORD)
            page.goto(f"{base}/admin/#/w04", wait_until="networkidle")
            page.locator("h1", has_text="用户与会员").wait_for()
            page.fill("#user-query", phone)
            page.click("button[data-act=user-search]")
            page.locator("text=何帆").wait_for()
            page.click("button[data-act=ban-open]")
            page.locator("#ban-type").wait_for()
            page.select_option("#ban-type", "permanent")
            page.fill("#ban-reason", "验收封禁")
            page.click("button[data-act=ban]")
            page.locator(".tag", has_text="已封禁").wait_for()
            with page.expect_navigation():
                page.click("button[data-act=logout]")
            page.locator("h2", has_text="账号登录").wait_for()
            assert "reset=" in page.url
            sign_in(page, DemoAccounts.SUPPORT, DemoAccounts.SUPPORT_PASSWORD)
            assert page.url.endswith("#/w02")
            evidence = page.locator("body").inner_text()
            assert phone not in evidence
            assert "9054" in evidence
            page.goto(f"{base}/admin/#/w04", wait_until="networkidle")
            page.fill("#user-query", phone)
            page.click("button[data-act=user-search]")
            page.locator(".tag", has_text="已封禁").wait_for()
            support_text = page.locator("body").inner_text()
            assert "永久" in support_text
            assert "验收封禁" in support_text
            assert phone not in support_text
            assert openid not in support_text
            assert "9054" in support_text
            page.goto(f"{base}/admin/#/w05", wait_until="networkidle")
            orders = page.locator("body").inner_text()
            assert phone not in orders
            assert openid not in orders
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def _hit_is(page, selector: str) -> bool:
    box = page.locator(selector).first.bounding_box()
    assert box, selector
    return page.evaluate(
        """([selector, x, y]) => {
          const target = document.querySelector(selector);
          const hit = document.elementFromPoint(x, y);
          if (!target || !hit) return false;
          return hit === target || target.contains(hit) || hit.contains(target);
        }""",
        [selector, box["x"] + box["width"] / 2, box["y"] + box["height"] / 2],
    )


def test_w02_filters_stay_clickable_beside_the_drawer():
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
                    raise AssertionError("server exited")
                time.sleep(0.2)
        else:
            raise AssertionError("workbench did not start")
        with playwright_sync.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{base}/admin/#/login", wait_until="networkidle")
            page.locator("h2", has_text="账号登录").wait_for()
            page.fill("input[name=username]", DemoAccounts.ADMIN)
            page.fill("input[name=password]", DemoAccounts.ADMIN_PASSWORD)
            page.locator("button[type=submit]").click()
            page.locator("h1", has_text="取证申请").wait_for()
            assert page.locator("aside.drawer").count() == 0
            assert page.locator("#evidence-from").get_attribute("placeholder") == "YYYY-MM-DD"
            row_pay = page.locator("button.btn-sm[data-act=pay]")
            if row_pay.count() == 0:
                page.locator("button[data-act=accept]").first.click()
                accepted = page.locator("tr").filter(has=page.get_by_text("已受理", exact=True))
                accepted.first.locator("button", has_text="查看").click()
                page.locator("#quote-amount").wait_for()
                page.locator("#quote-amount").fill("100")
                page.locator("button[data-act=quote]").click()
                page.locator("button.btn-sm[data-act=pay]").first.wait_for()
                page.locator("button[data-act=close-drawer]").click()
                page.locator("aside.drawer").wait_for(state="detached")
            page.locator("button", has_text="查看").first.click()
            page.locator("aside.drawer").wait_for()
            for selector in (
                "#evidence-search",
                "#evidence-vip-label",
                "#evidence-from",
                "#evidence-to",
                "button.btn-sm[data-act=accept]",
                "button.btn-sm[data-act=open-evidence]",
                "button.btn-sm[data-act=pay]",
            ):
                page.locator(selector).first.scroll_into_view_if_needed()
                assert _hit_is(page, selector), selector
            pay = page.locator("button.btn-sm[data-act=pay]").first.bounding_box()
            card = page.locator(".card.grow .card-bd.flush").bounding_box()
            assert pay["x"] + pay["width"] <= card["x"] + card["width"] + 1
            clipped = page.evaluate(
                """() => [...document.querySelectorAll("td.cell-no, td.cell-status")]
                  .filter((cell) => cell.scrollWidth > cell.clientWidth)
                  .map((cell) => cell.innerText)"""
            )
            assert clipped == []
            for selector in ("#evidence-from", "#evidence-to"):
                assert page.locator(selector).evaluate(
                    "(el) => getComputedStyle(el).minWidth"
                ) in {"132px"}
            page.locator("button.btn-sm[data-act=pay]").first.click()
            page.locator("button.btn-sm[data-act=pay]").wait_for(state="detached")
            page.locator("#evidence-vip-label").click()
            page.locator("#evidence-search").click()
            page.locator("td", has_text="南风知我意").wait_for(state="detached")
            assert page.locator("td", has_text="星河旧梦").count() >= 1
            page.locator("button[data-act=close-drawer]").click()
            page.locator("aside.drawer").wait_for(state="detached")
            page.locator("button", has_text="查看").first.click()
            page.locator("aside.drawer").wait_for()
            page.keyboard.press("Escape")
            page.locator("aside.drawer").wait_for(state="detached")
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()


def test_round3_tier_dates_profile_failure_and_refund():
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
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
                    raise AssertionError("server exited")
                time.sleep(0.2)
        else:
            raise AssertionError("workbench did not start")
        with playwright_sync.sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1440, "height": 900})
            page.goto(f"{base}/admin/#/login", wait_until="networkidle")
            page.fill("input[name=username]", DemoAccounts.ADMIN)
            page.fill("input[name=password]", DemoAccounts.ADMIN_PASSWORD)
            page.locator("button[type=submit]").click()
            page.locator("h1", has_text="取证申请").wait_for()
            page.locator("button.page-note", has_text="说明").wait_for()
            page.locator("tr", has_text="雾港来信").locator("button", has_text="查看").click()
            page.locator("aside.drawer").wait_for()
            assert page.locator("aside.drawer").get_by_text("普通会员", exact=True).count() == 1
            page.locator("button[data-act=close-drawer]").click()
            page.locator("aside.drawer").wait_for(state="detached")
            page.fill("#evidence-from", "2026-11-21")
            page.fill("#evidence-to", "2026-11-21")
            page.locator("#evidence-search").click()
            page.locator("td", has_text="星河旧梦").wait_for(state="detached")
            assert page.locator("td", has_text="QZ20261120041").count() == 1
            page.fill("#evidence-from", "2026-11-20")
            page.fill("#evidence-to", "2026-11-20")
            page.locator("#evidence-search").click()
            page.locator("td", has_text="QZ20261120041").wait_for(state="detached")
            page.fill("#evidence-from", "")
            page.fill("#evidence-to", "")
            page.locator("#evidence-search").click()
            page.locator("tr", has_text="雾港来信").locator("button", has_text="查看").click()
            page.locator("button[data-act=fail-open]").click()
            page.locator("button", has_text="确认失败并关闭").click()
            assert "invalid" in (page.locator("#fail-reason").get_attribute("class") or "")
            page.fill("#fail-reason", "用户取消")
            page.locator("button", has_text="确认失败并关闭").click()
            page.locator("tr", has_text="雾港来信").get_by_text("已关闭", exact=True).wait_for()
            working = page.locator("tr").filter(has=page.get_by_text("取证中", exact=True))
            working.first.locator("button", has_text="查看").click()
            page.locator("button[data-act=fail-open]").click()
            page.fill("#fail-reason", "片源无法核验")
            page.locator("button", has_text="确认失败并关闭").click()
            assert page.locator("#fail-reason").input_value() == "片源无法核验"
            assert "invalid" in (page.locator("#refund-note").get_attribute("class") or "")
            page.locator("button", has_text="取消").click()
            page.goto(f"{base}/admin/#/w04", wait_until="networkidle")
            page.fill("#user-query", "18600004419")
            page.locator("button[data-act=user-search]").click()
            page.locator("text=周安").wait_for()
            page.fill("#grant-reason", "验收开通")
            page.locator("button[data-act=grant]").click()
            page.locator(".tag", has_text="手动发放").wait_for()
            assert "输入手机号后查询" not in page.locator("body").inner_text()
            assert "周安" in page.locator("body").inner_text()
            page.goto(f"{base}/admin/#/w03", wait_until="networkidle")
            until = page.locator("#invite-until")
            assert until.get_attribute("type") != "date"
            assert until.input_value()
            assert len(until.input_value()) == 10 and until.input_value()[4] == "-"
            page.goto(f"{base}/admin/#/w05", wait_until="networkidle")
            drawer = page.locator("aside.drawer")
            drawer.get_by_text("发放异常", exact=True).wait_for()
            assert drawer.get_by_role("button", name="补发").count() == 1
            assert drawer.get_by_role("button", name="退款并回收权益").count() == 0
            issued = page.locator("tr").filter(has=page.get_by_text("已发放", exact=True))
            issued.first.locator("button", has_text="查看").click()
            drawer.get_by_role("button", name="退款并回收权益").wait_for()
            page.goto(f"{base}/admin/#/w08", wait_until="networkidle")
            page.locator("#log-type").select_option("evidence_request")

            def evidence_logs(response):
                return "object_type=evidence_request" in response.url

            with page.expect_response(evidence_logs):
                page.locator("button[data-act=log-search]").click()
            assert page.locator("td", has_text="登录").count() == 0
            assert page.locator("td", has_text="接单").count() >= 1
            browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
