from fastapi.testclient import TestClient
from sqlalchemy import func, select

from nadou.admin.content import SplashBook
from nadou.db import Database
from nadou.main import create_app
from nadou.models import SplashConfig
from nadou.public_config import PublicConfig
from tests.workbench_helpers import call


def splash_count(session) -> int:
    session.rollback()
    return session.scalar(select(func.count()).select_from(SplashConfig))


def test_public_splash_row_is_the_workbench_row():
    assert PublicConfig.ROW_ID == SplashBook.ROW_ID


def test_missing_splash_row_is_enabled_and_writes_nothing(workbench):
    assert splash_count(workbench.session) == 0
    response = workbench.client.get("/api/v1/app/config")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "public, max-age=60"
    assert response.json() == {"splash_enabled": True, "slogan": ""}
    assert splash_count(workbench.session) == 0


def test_public_config_needs_no_token(session, clock):
    app = create_app(database=Database(engine=session.get_bind()), now=lambda: clock)
    with TestClient(app) as client:
        bare = client.get("/api/v1/app/config")
        bogus = client.get(
            "/api/v1/app/config",
            headers={"Authorization": "Bearer not-a-session"},
        )
    assert bare.request.headers.get("authorization") is None
    assert bare.status_code == 200
    assert bogus.status_code == 200
    assert bare.json() == {"splash_enabled": True, "slogan": ""}
    assert bare.headers["cache-control"] == "public, max-age=60"
    assert splash_count(session) == 0


def test_public_config_reads_enabled_and_disabled(workbench):
    workbench.session.add(
        SplashConfig(
            id=PublicConfig.ROW_ID,
            enabled=True,
            image_name="internal-splash.png",
            jump_label="internal-jump",
            slogan="开着",
            updated_at=workbench.clock.now,
        )
    )
    workbench.session.commit()
    opened = workbench.client.get("/api/v1/app/config")
    assert opened.status_code == 200
    assert opened.json() == {"splash_enabled": True, "slogan": "开着"}
    assert "internal-splash.png" not in opened.text
    assert "internal-jump" not in opened.text

    row = workbench.session.get(SplashConfig, PublicConfig.ROW_ID)
    row.enabled = False
    row.slogan = "关着"
    workbench.session.commit()
    closed = workbench.client.get("/api/v1/app/config")
    assert closed.json() == {"splash_enabled": False, "slogan": "关着"}
    assert splash_count(workbench.session) == 1


def test_workbench_put_shows_up_on_the_next_public_read(workbench):
    assert splash_count(workbench.session) == 0
    before = workbench.client.get("/api/v1/app/config")
    assert before.json()["splash_enabled"] is True
    assert splash_count(workbench.session) == 0

    call(
        workbench,
        "PUT",
        "/api/v1/admin/content/splash",
        workbench.admin_token,
        json={
            "enabled": False,
            "image_name": "splash_demo.png",
            "slogan": "片单检索",
            "jump_label": "去首页",
        },
    )
    disabled = workbench.client.get("/api/v1/app/config")
    assert disabled.status_code == 200
    assert disabled.json() == {"splash_enabled": False, "slogan": "片单检索"}
    assert "splash_demo.png" not in disabled.text
    assert "去首页" not in disabled.text

    call(
        workbench,
        "PUT",
        "/api/v1/admin/content/splash",
        workbench.admin_token,
        json={
            "enabled": True,
            "image_name": "splash_demo.png",
            "slogan": "片单检索",
            "jump_label": None,
        },
    )
    enabled = workbench.client.get("/api/v1/app/config")
    assert enabled.json() == {"splash_enabled": True, "slogan": "片单检索"}
    assert splash_count(workbench.session) == 1
