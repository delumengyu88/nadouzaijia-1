from nadou.db import Base


def test_schema_has_no_zip_or_download_tables():
    names = set(Base.metadata.tables)
    assert names == {
        "users",
        "entitlements",
        "tasks",
        "index_records",
        "platform_aliases",
        "program_aliases",
        "staff_accounts",
        "staff_login_locks",
        "staff_sessions",
        "audit_logs",
        "evidence_requests",
        "evidence_notes",
        "orders",
        "order_issuances",
        "index_runs",
        "index_run_locks",
        "index_alert_episodes",
    }
    assert not any("zip" in name or "download" in name or "archive" in name for name in names)
