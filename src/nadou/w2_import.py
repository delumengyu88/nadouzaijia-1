"""Import a validated W2 release (program_index.db) into index_records.

The puller on the host downloads and validates a release, then points
``/var/lib/w2-index/current`` at it. This command reads that directory read-only and
makes it the searchable data in one database transaction:

* Stable key: (task_id, canonical program_name), the existing unique key of
  index_records. Record ids are uuid5 of that key, so a re-import keeps every id that
  entitlements, query history, evidence requests and reports already point to.
  Nothing is deleted. A row missing from a new release only stops being shown.
* Visibility uses the existing approval switch: each record carries the release's
  sha256 in ``hitlist_sha256`` and its task's ``approved_hitlist_sha256`` is set to
  the same digest. Rows from older releases no longer match and stay hidden.
* Only consensus rows are user-facing (unchanged search rule). Stop-word titles from
  exclude_list.json are not imported; low-confidence titles are stored as ``review``.
* Rows whose detection date is less than 30 days old and that came from an earlier
  W2 import keep the display state they had before the rebuild: a shown row stays
  shown with its previous content, a hidden one stays hidden. Brand-new recent rows,
  and rows that came from another source (first W2 import), are written normally.
* Tasks carrying a single-row summary grant are left alone, because changing their
  approval would invalidate that grant.

A failure rolls everything back, so users keep searching the previous release.
"""

import argparse
import hashlib
import json
import os
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from nadou.filename_parser import FilenameParser
from nadou.importer import TaskExportImporter, TestTaskFilter
from nadou.models import IndexRecord, Task, W2IndexImport
from nadou.normalize import ProgramNormalizer
from nadou.platform_names import PlatformNames
from nadou.records import IndexRecordStore
from nadou.review import ReviewTitles
from nadou.summary_source import clocks
from nadou.text import RecordAge


class W2ImportError(ValueError):
    """The release cannot be imported. The previous data keeps serving."""


REQUIRED_COLUMNS = (
    "program_name",
    "task_id",
    "hit_type",
    "frames",
    "timestamps",
    "created_at",
    "filename",
    "status",
)
HIT_TYPES = frozenset({"consensus", "dispute", "review"})
FREEZE_DAYS = 30
_CHUNK = 500
_ADVISORY_KEY = 9142030


@dataclass
class W2Row:
    task_id: str
    program_name: str
    program_name_raw: str
    hit_type: str
    frame_count: int
    timestamps: list[str]
    created_at: datetime | None
    filename: str
    status: str


@dataclass
class W2Release:
    version: str
    db_sha256: str
    manifest: dict
    rows: dict[tuple[str, str], W2Row]
    stop_words: frozenset[str]
    low_confidence: frozenset[str]
    verified_excludes: frozenset[str]
    hit_rows: int
    kept_consensus_names: int

    @property
    def consensus_names(self) -> int | None:
        value = self.manifest.get("consensus_names")
        return value if isinstance(value, int) else None


@dataclass
class W2ImportStats:
    source_rows: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    frozen: int = 0
    carried: int = 0
    excluded: int = 0
    low_confidence: int = 0
    skipped_tasks: int = 0
    skipped_rows: int = 0
    new_tasks: int = 0
    managed_tasks: int = 0
    retired_tasks: int = 0
    protected_tasks: int = 0
    skipped: bool = False
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        data = {k: v for k, v in self.__dict__.items() if k != "extra"}
        data.update(self.extra)
        return data


class ExcludeList:
    """exclude_list.json from W2 (schema 2):

    ``{"stopwords": [...], "low_confidence": [...], "verified_excludes": [...]}``

    * stopwords: the whole title is dropped.
    * low_confidence: kept in the index but never shown (stored as ``review``).
    * verified_excludes: dropped, but only for rows 30 days or older.

    Also accepted: a plain list of titles (all stop words), or lists of objects with
    name/program_name/title and an optional type/kind/reason.
    """

    NAME_KEYS = ("program_name", "name", "title", "word")
    KIND_KEYS = ("type", "kind", "reason", "category")

    @staticmethod
    def _low(label: str) -> bool:
        folded = label.casefold()
        return "low" in folded or "低置信" in label

    @staticmethod
    def _verified(label: str) -> bool:
        return "verified" in label.casefold()

    @classmethod
    def parse(cls, data) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
        stop: set[str] = set()
        low: set[str] = set()
        verified: set[str] = set()

        def add(target: set[str], value) -> None:
            if not isinstance(value, str):
                raise W2ImportError("exclude_list.json 的片名必须是文字")
            canonical = ProgramNormalizer.canonical(value)
            if canonical:
                target.add(canonical)

        def add_items(items, default: set[str]) -> None:
            if not isinstance(items, list):
                raise W2ImportError("exclude_list.json 格式无法识别")
            for item in items:
                if isinstance(item, str):
                    add(default, item)
                elif isinstance(item, dict):
                    name = next((item[k] for k in cls.NAME_KEYS if k in item), None)
                    kind = next((str(item[k]) for k in cls.KIND_KEYS if k in item), "")
                    add(low if kind and cls._low(kind) else default, name)
                else:
                    raise W2ImportError("exclude_list.json 格式无法识别")

        if isinstance(data, list):
            add_items(data, stop)
        elif isinstance(data, dict):
            for key, items in data.items():
                if not isinstance(items, list):
                    continue
                label = str(key)
                if cls._verified(label):
                    add_items(items, verified)
                else:
                    add_items(items, low if cls._low(label) else stop)
        else:
            raise W2ImportError("exclude_list.json 格式无法识别")
        return frozenset(stop), frozenset(low), frozenset(verified)


class W2ReleaseReader:
    """Read and re-check one release directory without writing to it."""

    @staticmethod
    def sha256(path: Path) -> str:
        with path.open("rb") as f:
            return hashlib.file_digest(f, "sha256").hexdigest()

    @staticmethod
    def md5(path: Path) -> str:
        with path.open("rb") as f:
            return hashlib.file_digest(f, "md5").hexdigest()

    @classmethod
    def read(cls, source: Path) -> W2Release:
        directory = source.resolve()
        if not directory.is_dir():
            raise W2ImportError(f"找不到数据目录 {source}")
        db = directory / "program_index.db"
        manifest_path = directory / "manifest.json"
        exclude_path = directory / "exclude_list.json"
        for path in (db, manifest_path, exclude_path):
            if not path.is_file():
                raise W2ImportError(f"缺少文件 {path.name}")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            exclude = json.loads(exclude_path.read_text(encoding="utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise W2ImportError(f"manifest/exclude_list 不是有效 JSON：{exc}") from exc
        if not isinstance(manifest, dict):
            raise W2ImportError("manifest.json 格式无法识别")
        version = str(manifest.get("version") or directory.name).strip()
        if not version or len(version) > 64:
            raise W2ImportError("无法确定版本号")
        want_md5 = (manifest.get("md5") or {}).get("program_index.db", "")
        if not want_md5 or cls.md5(db) != want_md5:
            raise W2ImportError("program_index.db 的 MD5 与 manifest 不一致")
        stop_words, low_confidence, verified = ExcludeList.parse(exclude)
        # The manifest repeats the lists it applied; honour both.
        extra_stop, extra_low, _ = ExcludeList.parse(
            {
                "stopwords": manifest.get("excluded_stopwords") or [],
                "low_confidence": manifest.get("excluded_lowconf") or [],
            }
        )
        stop_words |= extra_stop
        low_confidence |= extra_low
        raw_rows = cls._rows(db)
        hit_rows = len(raw_rows)
        if manifest.get("hit_rows") != hit_rows:
            raise W2ImportError(f"命中行数 {hit_rows} 与 manifest 的 hit_rows 不一致")
        kept = len({r[0] for r in raw_rows if (r[2] or "").strip() == "consensus"})
        if manifest.get("kept_consensus_names") != kept:
            raise W2ImportError(f"共识片名数 {kept} 与 manifest 的 kept_consensus_names 不一致")
        rows: dict[tuple[str, str], W2Row] = {}
        for raw in raw_rows:
            row = cls._row(raw)
            if row is None:
                continue
            key = (row.task_id, row.program_name)
            previous = rows.get(key)
            rows[key] = row if previous is None else cls._merge(previous, row)
        return W2Release(
            version=version,
            db_sha256=cls.sha256(db),
            manifest=manifest,
            rows=rows,
            stop_words=stop_words,
            low_confidence=low_confidence,
            verified_excludes=verified,
            hit_rows=hit_rows,
            kept_consensus_names=kept,
        )

    @staticmethod
    def _rows(db: Path) -> list[tuple]:
        uri = db.as_uri() + "?mode=ro&immutable=1"
        try:
            conn = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as exc:
            raise W2ImportError(f"program_index.db 打不开：{exc}") from exc
        try:
            columns = {r[1] for r in conn.execute("PRAGMA table_info(program_index)")}
            missing = [c for c in REQUIRED_COLUMNS if c not in columns]
            if missing:
                raise W2ImportError(f"program_index 缺列 {missing}")
            return conn.execute(
                "SELECT " + ",".join(REQUIRED_COLUMNS) + " FROM program_index"
            ).fetchall()
        except sqlite3.Error as exc:
            raise W2ImportError(f"program_index.db 读取失败：{exc}") from exc
        finally:
            conn.close()

    @staticmethod
    def _row(raw: tuple) -> W2Row | None:
        program, task_id, hit_type, frames, stamps, created, filename, status = raw
        task_id = str(task_id or "").strip()
        name_raw = str(program or "").strip()
        canonical = ProgramNormalizer.canonical(name_raw)
        if not task_id or not canonical or len(task_id) > 64 or len(canonical) > 200:
            return None
        kind = str(hit_type or "").strip().casefold()
        if kind not in HIT_TYPES:
            kind = "review"
        try:
            frame_count = max(int(float(frames)), 0) if frames not in (None, "") else 0
        except (TypeError, ValueError):
            frame_count = 0
        return W2Row(
            task_id=task_id,
            program_name=canonical,
            program_name_raw=name_raw[:200],
            hit_type=kind,
            frame_count=frame_count,
            timestamps=clocks([str(stamps)]) if stamps else [],
            created_at=W2ReleaseReader.parse_time(created),
            filename=str(filename or "").strip()[:500],
            status=str(status or "").strip().casefold(),
        )

    @staticmethod
    def parse_time(value) -> datetime | None:
        if value in (None, ""):
            return None
        try:
            parsed = datetime.fromisoformat(str(value).strip())
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=RecordAge.TZ)
        return parsed

    @staticmethod
    def _merge(a: W2Row, b: W2Row) -> W2Row:
        # W2 keeps a consensus row and a dispute row for the same task and title.
        # The better-ranked row wins as a whole; only equal-ranked rows are merged.
        rank = {"consensus": 0, "review": 1, "dispute": 2}
        if rank[a.hit_type] != rank[b.hit_type]:
            return a if rank[a.hit_type] < rank[b.hit_type] else b
        best = a
        stamps = list(dict.fromkeys(a.timestamps + b.timestamps))
        return W2Row(
            task_id=a.task_id,
            program_name=a.program_name,
            program_name_raw=best.program_name_raw,
            hit_type=best.hit_type,
            frame_count=max(a.frame_count, b.frame_count),
            timestamps=stamps,
            created_at=a.created_at or b.created_at,
            filename=a.filename or b.filename,
            status=a.status or b.status,
        )


class W2Importer:
    def __init__(self, session: Session, now: datetime) -> None:
        self.session = session
        self.now = now

    # --- queries -----------------------------------------------------------------
    def current(self) -> W2IndexImport | None:
        return self.session.scalar(
            select(W2IndexImport)
            .where(W2IndexImport.status == "success")
            .order_by(W2IndexImport.finished_at.desc(), W2IndexImport.id.desc())
            .limit(1)
        )

    def _w2_digests(self) -> set[str]:
        return {
            d
            for d in self.session.scalars(
                select(W2IndexImport.db_sha256).where(W2IndexImport.status == "success")
            )
            if d
        }

    @staticmethod
    def _chunks(values: Iterable[str]) -> Iterable[list[str]]:
        items = sorted(values)
        for start in range(0, len(items), _CHUNK):
            yield items[start : start + _CHUNK]

    # --- import ------------------------------------------------------------------
    def apply(self, release: W2Release, *, force: bool = False) -> W2ImportStats:
        """Apply inside the caller's transaction. The caller commits or rolls back."""
        stats = W2ImportStats(source_rows=release.hit_rows)
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _ADVISORY_KEY})
        current = self.current()
        if (
            not force
            and current is not None
            and current.version == release.version
            and current.db_sha256 == release.db_sha256
        ):
            stats.skipped = True
            return stats

        digest = release.db_sha256
        previous_digests = self._w2_digests()
        review_titles = ReviewTitles.load(self.session)
        snapshot_tasks = {task_id for task_id, _ in release.rows}

        protected = set(
            self.session.scalars(
                select(IndexRecord.task_id).where(IndexRecord.summary_grant.is_not(None)).distinct()
            )
        )
        retired = set()
        if previous_digests:
            retired = (
                set(
                    self.session.scalars(
                        select(Task.task_id).where(
                            Task.approved_hitlist_sha256.in_(sorted(previous_digests))
                        )
                    )
                )
                - snapshot_tasks
            )
        managed = (snapshot_tasks | retired) - protected
        stats.protected_tasks = len((snapshot_tasks | retired) & protected)
        stats.retired_tasks = len(retired - protected)
        stats.managed_tasks = len(managed)

        tasks: dict[str, Task] = {}
        records: dict[tuple[str, str], IndexRecord] = {}
        for chunk in self._chunks(managed):
            for task in self.session.scalars(select(Task).where(Task.task_id.in_(chunk))):
                tasks[task.task_id] = task
            for record in self.session.scalars(
                select(IndexRecord).where(IndexRecord.task_id.in_(chunk))
            ):
                records[(record.task_id, record.program_name)] = record

        # Display state before this import, used by the 30-day freeze.
        shown_before = {
            key for key, record in records.items() if self._shown(record, tasks.get(record.task_id))
        }

        self._ensure_tasks(release, managed, tasks, stats)

        touched: set[tuple[str, str]] = set()
        for key, row in release.rows.items():
            if row.task_id not in managed:
                continue
            if row.task_id not in tasks:
                # No task metadata anywhere yet; the next import picks it up once the
                # hourly task CSV has created the task.
                stats.skipped_rows += 1
                continue
            task = tasks[row.task_id]
            if row.program_name in release.stop_words or (
                row.program_name in release.verified_excludes
                and not self._recent_time(task.created_at or row.created_at)
            ):
                stats.excluded += 1
                continue
            touched.add(key)
            hit_type = row.hit_type
            if row.program_name in release.low_confidence:
                if hit_type == "consensus":
                    stats.low_confidence += 1
                hit_type = "review"
            elif hit_type == "consensus" and row.program_name in review_titles:
                hit_type = "review"
            existing = records.get(key)
            if (
                existing is not None
                and existing.hitlist_sha256 in previous_digests
                and self._recent(existing, task)
            ):
                stats.frozen += 1
                if key in shown_before and existing.hitlist_sha256 != digest:
                    existing.hitlist_sha256 = digest
                continue
            if existing is None:
                self.session.add(
                    IndexRecord(
                        id=IndexRecordStore.public_id(row.task_id, row.program_name),
                        task_id=row.task_id,
                        program_name=row.program_name,
                        program_name_raw=row.program_name_raw,
                        hit_type=hit_type,
                        hitlist_sha256=digest,
                        frame_count=row.frame_count,
                        timestamps=list(row.timestamps),
                        date_generated=False,
                    )
                )
                stats.inserted += 1
                continue
            changed = False
            for attr, value in (
                ("program_name_raw", row.program_name_raw),
                ("hit_type", hit_type),
                ("frame_count", row.frame_count),
                ("timestamps", list(row.timestamps)),
                ("hitlist_sha256", digest),
            ):
                if getattr(existing, attr) != value:
                    setattr(existing, attr, value)
                    changed = True
            if changed:
                stats.updated += 1
            else:
                stats.unchanged += 1

        # Rows of managed tasks that the release no longer has: recent shown rows are
        # carried forward unchanged, everything else stops being shown (never deleted).
        for key in shown_before - touched:
            record = records[key]
            task = tasks.get(record.task_id)
            if record.program_name in release.stop_words:
                continue
            if record.hitlist_sha256 in previous_digests and self._recent(record, task):
                record.hitlist_sha256 = digest
                stats.carried += 1

        for task_id in managed:
            task = tasks.get(task_id)
            if task is not None and task.approved_hitlist_sha256 != digest:
                task.approved_hitlist_sha256 = digest
        self.session.flush()
        return stats

    def _ensure_tasks(
        self,
        release: W2Release,
        managed: set[str],
        tasks: dict[str, Task],
        stats: W2ImportStats,
    ) -> None:
        firsts: dict[str, W2Row] = {}
        for (task_id, _), row in release.rows.items():
            if task_id in managed:
                firsts.setdefault(task_id, row)
        for task_id, row in firsts.items():
            task = tasks.get(task_id)
            if task is None:
                if row.status not in TaskExportImporter.STATUSES:
                    stats.skipped_tasks += 1
                    continue
                parsed = FilenameParser.parse(row.filename)
                normalized = PlatformNames.normalize(parsed.platform)
                task = Task(
                    task_id=task_id,
                    status=row.status,
                    created_at=row.created_at,
                    raw_filename=row.filename,
                    network_type=parsed.network_type,
                    platform_name=normalized.name,
                    platform_province=normalized.province,
                    platform_operator=normalized.operator,
                    platform_subtype=normalized.subtype,
                    source_unverified=normalized.unrecognized,
                    is_test=TestTaskFilter.excluded(row.filename, parsed.platform, normalized.name),
                )
                self.session.add(task)
                tasks[task_id] = task
                stats.new_tasks += 1
                continue
            # The hourly task CSV stays authoritative. Only fill gaps it left.
            if task.created_at is None and row.created_at is not None:
                task.created_at = row.created_at
                self.session.execute(
                    update(IndexRecord)
                    .where(IndexRecord.task_id == task_id, IndexRecord.date_generated.is_(True))
                    .values(generated_uploaded_at=None, date_generated=False)
                    .execution_options(synchronize_session="fetch")
                )
            if not task.raw_filename and row.filename:
                task.raw_filename = row.filename

    @staticmethod
    def _shown(record: IndexRecord, task: Task | None) -> bool:
        return (
            task is not None
            and record.summary_grant is None
            and record.hit_type == "consensus"
            and bool(task.approved_hitlist_sha256)
            and record.hitlist_sha256 == task.approved_hitlist_sha256
        )

    def _recent(self, record: IndexRecord, task: Task | None) -> bool:
        when = task.created_at if task is not None and task.created_at else record.uploaded_at
        return self._recent_time(when)

    def _recent_time(self, when: datetime | None) -> bool:
        return when is not None and RecordAge.days(when, self.now) < FREEZE_DAYS


class W2ImportLog:
    @staticmethod
    def record(
        session: Session,
        *,
        release: W2Release | None,
        version: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        stats: dict | None = None,
        error: str | None = None,
    ) -> W2IndexImport:
        build_ts = str(release.manifest.get("build_ts") or "")[:40] if release else ""
        row = W2IndexImport(
            version=(release.version if release else version)[:64] or "未知",
            db_sha256=release.db_sha256 if release else None,
            build_ts=build_ts or None,
            consensus_names=release.consensus_names if release else None,
            kept_consensus_names=release.kept_consensus_names if release else None,
            hit_rows=release.hit_rows if release else None,
            status=status,
            stats=stats,
            error_summary=error[:500] if error else None,
            started_at=started_at,
            finished_at=finished_at,
        )
        session.add(row)
        return row

    @staticmethod
    def payload(session: Session) -> dict:
        current = W2Importer(session, datetime.now(RecordAge.TZ)).current()
        last = session.scalar(
            select(W2IndexImport)
            .order_by(W2IndexImport.finished_at.desc(), W2IndexImport.id.desc())
            .limit(1)
        )

        def row(item: W2IndexImport | None) -> dict | None:
            if item is None:
                return None
            return {
                "version": item.version,
                "status": item.status,
                "db_sha256": item.db_sha256,
                "build_ts": item.build_ts,
                "consensus_names": item.consensus_names,
                "kept_consensus_names": item.kept_consensus_names,
                "hit_rows": item.hit_rows,
                "stats": item.stats,
                "error": item.error_summary,
                "finished_at": item.finished_at.isoformat(),
            }

        return {"current": row(current), "last_attempt": row(last)}


def import_release(
    session_factory: Callable[[], Session],
    source: Path,
    now: datetime,
    *,
    force: bool = False,
) -> tuple[W2ImportStats, W2Release]:
    """Read, apply and log one release. Raises W2ImportError after logging a failure."""
    started = now
    release: W2Release | None = None
    try:
        release = W2ReleaseReader.read(source)
        with session_factory() as session, session.begin():
            stats = W2Importer(session, now).apply(release, force=force)
            if not stats.skipped:
                W2ImportLog.record(
                    session,
                    release=release,
                    version=release.version,
                    status="success",
                    started_at=started,
                    finished_at=datetime.now(RecordAge.TZ),
                    stats=stats.as_dict(),
                )
        return stats, release
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        try:
            with session_factory() as session, session.begin():
                W2ImportLog.record(
                    session,
                    release=release,
                    version=Path(source).resolve().name,
                    status="failed",
                    started_at=started,
                    finished_at=datetime.now(RecordAge.TZ),
                    error=message,
                )
        except Exception:  # noqa: BLE001 - the original error matters more
            pass
        if isinstance(exc, W2ImportError):
            raise
        raise W2ImportError(message) from exc


def run(argv: list[str]) -> int:
    from nadou.config import get_settings
    from nadou.db import Database

    parser = argparse.ArgumentParser(
        prog="w2-import", description="把已校验的 W2 索引版本导入检索库（单事务，失败保留旧数据）"
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=Path(os.environ.get("NADOU_W2_CURRENT", "/w2-index/current")),
        help="版本目录，默认 /w2-index/current",
    )
    parser.add_argument("--force", action="store_true", help="同一版本也重新导入")
    parser.add_argument("--status", action="store_true", help="只显示当前已导入的版本")
    args = parser.parse_args(argv)
    settings = get_settings()
    database = Database(settings.database_url)
    try:
        if args.status:
            with database.session() as session:
                print(json.dumps(W2ImportLog.payload(session), ensure_ascii=False, indent=2))
            return 0
        try:
            stats, release = import_release(
                database.session, args.source, datetime.now(RecordAge.TZ), force=args.force
            )
        except W2ImportError as exc:
            # 钉钉报警由宿主机上的 w2_import_runner.py 去重后发送
            print(f"导入失败，线上继续使用上一版数据：{exc}")
            return 1
        if stats.skipped:
            print(f"版本 {release.version} 已是当前版本，无需导入")
            return 0
        print(
            f"导入完成：版本 {release.version}，对外共识片名 {release.consensus_names}，"
            f"新增 {stats.inserted}，更新 {stats.updated}，未变 {stats.unchanged}，"
            f"30 天内锁定 {stats.frozen + stats.carried}，停用词剔除 {stats.excluded}"
        )
        return 0
    finally:
        database.engine.dispose()
