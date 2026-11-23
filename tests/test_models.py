from nadou.db import Base


def test_schema_has_no_zip_or_download_tables():
    names = set(Base.metadata.tables)
    assert names == {
        "users",
        "user_sessions",
        "entitlements",
        "tasks",
        "index_records",
        "review_program_titles",
        "platform_aliases",
        "program_aliases",
        "staff_accounts",
        "staff_login_locks",
        "staff_sessions",
        "audit_logs",
        "evidence_requests",
        "evidence_notes",
        "orders",
        "virtual_orders",
        "order_issuances",
        "index_runs",
        "w2_index_imports",
        "index_run_locks",
        "index_alert_episodes",
        "invite_codes",
        "splash_config",
        "announcements",
        "homepage_examples",
        "public_example_permits",
        "member_queries",
        "query_programs",
    }
    assert not any("zip" in name or "download" in name or "archive" in name for name in names)
