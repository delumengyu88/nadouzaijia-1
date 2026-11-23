"""Build small synthetic W2 releases for importer and puller tests."""

import hashlib
import json
import sqlite3
from pathlib import Path

COLUMNS = (
    "program_name",
    "task_id",
    "hit_type",
    "frames",
    "timestamps",
    "created_at",
    "filename",
    "status",
)


def build_release(
    directory: Path,
    rows: list[tuple],
    *,
    version: str = "20261121_041700",
    build_ts: str = "2026-11-21 04:17:00",
    stopwords: tuple[str, ...] = ("怪",),
    low_confidence: tuple[str, ...] = ("家庭",),
    verified: tuple[str, ...] = (),
    consensus_names: int | None = None,
    drop_columns: tuple[str, ...] = (),
    manifest_overrides: dict | None = None,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    db = directory / "program_index.db"
    if db.exists():
        db.unlink()
    columns = [c for c in COLUMNS if c not in drop_columns]
    with sqlite3.connect(db) as conn:
        conn.execute(f"CREATE TABLE program_index ({', '.join(c + ' TEXT' for c in columns)})")
        conn.execute("CREATE TABLE program_summary (program_name TEXT PRIMARY KEY, hit_count INT)")
        for row in rows:
            values = dict(zip(COLUMNS, row, strict=True))
            conn.execute(
                f"INSERT INTO program_index VALUES ({','.join('?' * len(columns))})",
                [values[c] for c in columns],
            )
    conn.close()
    kept = len({r[0] for r in rows if r[2] == "consensus"})
    manifest = {
        "version": version,
        "build_ts": build_ts,
        "consensus_names": kept - len(set(low_confidence) & {r[0] for r in rows})
        if consensus_names is None
        else consensus_names,
        "kept_consensus_names": kept,
        "hit_rows": len(rows),
        "md5": {"program_index.db": hashlib.md5(db.read_bytes()).hexdigest()},
    }
    manifest.update(manifest_overrides or {})
    (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False), "utf-8")
    (directory / "exclude_list.json").write_text(
        json.dumps(
            {
                "version": "20261122",
                "stopwords": list(stopwords),
                "low_confidence": list(low_confidence),
                "verified_excludes": list(verified),
            },
            ensure_ascii=False,
        ),
        "utf-8",
    )
    for name in ("program_index.json", "consensus_names.csv", "consensus_names_with_count.csv"):
        (directory / name).write_text("[]" if name.endswith(".json") else "", "utf-8")
    return directory
