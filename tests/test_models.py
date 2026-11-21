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
    }
    assert not any("zip" in name or "download" in name or "archive" in name for name in names)
