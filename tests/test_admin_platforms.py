from sqlalchemy import func, select
from tests.workbench_helpers import add_unverified_task, call

from nadou.models import AuditLog, PlatformAlias, Task


def test_alias_conflict_names_the_existing_platform_and_does_not_overwrite(workbench):
    token = workbench.admin_token
    created = call(
        workbench,
        "POST",
        "/api/v1/admin/platforms",
        token,
        json={
            "network_type": "公网",
            "standard_name": "示例甲",
            "aliases": ["示例甲别名"],
            "enabled": True,
        },
        expected=201,
    ).json()
    assert created["standard_name"] == "示例甲"
    assert created["aliases"] == ["示例甲别名"]
    assert created["modified_by"] == "wb-admin"
    conflict = call(
        workbench,
        "POST",
        "/api/v1/admin/platforms",
        token,
        json={
            "network_type": "公网",
            "standard_name": "示例乙",
            "aliases": ["示例甲别名"],
        },
        expected=409,
    ).json()
    assert conflict["detail"]["existing_standard_name"] == "示例甲"
    assert conflict["detail"]["alias"] == "示例甲别名"
    other_network = call(
        workbench,
        "POST",
        "/api/v1/admin/platforms",
        token,
        json={"network_type": "专网", "standard_name": "示例乙", "aliases": ["示例甲别名"]},
        expected=201,
    ).json()
    assert other_network["network_type"] == "专网"
    again = call(
        workbench,
        "POST",
        f"/api/v1/admin/platforms/{created['id']}/aliases",
        token,
        json={"alias": "示例甲别名"},
    ).json()
    assert again["aliases"] == ["示例甲别名"]
    third = call(
        workbench,
        "POST",
        "/api/v1/admin/platforms",
        token,
        json={"network_type": "公网", "standard_name": "示例丙", "aliases": []},
        expected=201,
    ).json()
    stolen = call(
        workbench,
        "POST",
        f"/api/v1/admin/platforms/{third['id']}/aliases",
        token,
        json={"alias": "示例甲别名"},
        expected=409,
    ).json()
    assert stolen["detail"]["existing_standard_name"] == "示例甲"
    moved = call(
        workbench,
        "POST",
        f"/api/v1/admin/platforms/{other_network['id']}/aliases",
        token,
        json={"alias": "示例甲"},
    ).json()
    assert "示例甲" in moved["aliases"]
    workbench.session.expire_all()
    owner = workbench.session.scalar(
        select(PlatformAlias).where(
            PlatformAlias.network_type == "公网",
            PlatformAlias.alias == "示例甲别名",
        )
    )
    assert owner.canonical_name == "示例甲"
    assert (
        workbench.session.scalar(
            select(func.count())
            .select_from(PlatformAlias)
            .where(PlatformAlias.network_type == "公网", PlatformAlias.alias == "示例甲别名")
        )
        == 1
    )
    call(
        workbench,
        "PATCH",
        f"/api/v1/admin/platforms/{created['id']}",
        token,
        json={"enabled": False},
    )
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert {"create_platform", "add_platform_alias", "update_platform"} <= actions


def test_backfill_rejects_a_disabled_platform(workbench):
    token = workbench.admin_token
    disabled = call(
        workbench,
        "POST",
        "/api/v1/admin/platforms",
        token,
        json={"network_type": "公网", "standard_name": "示例停用", "enabled": False},
        expected=201,
    ).json()
    assert disabled["enabled"] is False
    add_unverified_task(workbench.session, "task-disabled", "公网")
    call(
        workbench,
        "POST",
        "/api/v1/admin/platforms/backfill",
        token,
        json={"task_id": "task-disabled", "network_type": "公网", "standard_name": "示例停用"},
        expected=409,
    )
    workbench.session.expire_all()
    blocked = workbench.session.get(Task, "task-disabled")
    assert blocked.source_unverified is True
    assert blocked.platform_name is None

    call(
        workbench,
        "POST",
        "/api/v1/admin/platforms",
        token,
        json={"network_type": "公网", "standard_name": "示例启用", "enabled": True},
        expected=201,
    )
    add_unverified_task(workbench.session, "task-enabled", "公网")
    add_unverified_task(workbench.session, "task-other-net", "专网")
    call(
        workbench,
        "POST",
        "/api/v1/admin/platforms/backfill",
        token,
        json={"task_id": "task-other-net", "network_type": "公网", "standard_name": "示例启用"},
        expected=409,
    )
    updated = call(
        workbench,
        "POST",
        "/api/v1/admin/platforms/backfill",
        token,
        json={"task_id": "task-enabled", "network_type": "公网", "standard_name": "示例启用"},
    ).json()
    assert updated["source_unverified"] is False
    assert updated["platform_name"] == "示例启用"
    workbench.session.expire_all()
    assert workbench.session.get(Task, "task-other-net").source_unverified is True
    actions = set(workbench.session.scalars(select(AuditLog.action)).all())
    assert "backfill_platform" in actions
