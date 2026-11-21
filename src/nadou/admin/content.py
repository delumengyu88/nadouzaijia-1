from datetime import datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.models import Announcement, HomepageExample, IndexRecord, SplashConfig, Task


class SplashBook:
    ROW_ID = 1
    BANNED = ("永久免费", "全平台")

    def __init__(self, session: Session, audit: AuditWriter, now: datetime) -> None:
        self.session = session
        self.audit = audit
        self.now = now

    def get(self) -> SplashConfig:
        row = self.session.get(SplashConfig, SplashBook.ROW_ID)
        if row is None:
            row = SplashConfig(
                id=SplashBook.ROW_ID,
                enabled=False,
                image_name="",
                jump_label=None,
                slogan="",
                updated_at=self.now,
            )
            self.session.add(row)
            self.session.flush()
        return row

    def save(
        self, *, enabled: bool, image_name: str, jump_label: str | None, slogan: str
    ) -> SplashConfig:
        text = slogan.strip()
        image = image_name.strip()
        if len(text) > 80:
            raise WorkbenchError(400, "slogan is too long")
        if any(phrase in text for phrase in SplashBook.BANNED):
            raise WorkbenchError(400, "slogan uses a forbidden phrase")
        if len(image) > 200:
            raise WorkbenchError(400, "image name is too long")
        row = self.get()
        before = SplashView.render(row)
        row.enabled = enabled
        row.image_name = image
        row.jump_label = (jump_label or "").strip() or None
        row.slogan = text
        row.updated_at = self.now
        self.session.flush()
        self.audit.write("splash", str(row.id), "update_splash", before, SplashView.render(row))
        return row


class SplashView:
    @staticmethod
    def render(row: SplashConfig) -> dict:
        return {
            "enabled": row.enabled,
            "image_name": row.image_name,
            "jump_label": row.jump_label,
            "slogan": row.slogan,
            "dwell_seconds": 5,
            "updated_at": row.updated_at.isoformat(),
        }


class AnnouncementBook:
    def __init__(self, session: Session, audit: AuditWriter, now: datetime) -> None:
        self.session = session
        self.audit = audit
        self.now = now

    def list_rows(self) -> list[dict]:
        rows = self.session.scalars(
            select(Announcement).order_by(Announcement.starts_at.desc(), Announcement.id)
        ).all()
        rendered = [AnnouncementView.render(row, self.now) for row in rows]
        active = [item for item in rendered if item["state"] == "生效中"]
        if len(active) > 1:
            newest = max(active, key=lambda item: item["starts_at"])
            for item in active:
                if item["id"] != newest["id"]:
                    item["state"] = "已被更新覆盖"
        return rendered

    def save(
        self,
        *,
        title: str,
        body: str,
        button_text: str,
        jump_label: str | None,
        starts_at: datetime,
        ends_at: datetime,
        frequency: str,
        online: bool,
        announcement_id: str | None = None,
    ) -> Announcement:
        heading = title.strip()
        text = body.strip()
        button = button_text.strip()
        if not heading or len(heading) > 80:
            raise WorkbenchError(400, "title is required")
        if not text or len(text) > 200:
            raise WorkbenchError(400, "body must be 1 to 200 characters")
        if not button or len(button) > 32:
            raise WorkbenchError(400, "button text is required")
        if frequency not in {"once", "every"}:
            raise WorkbenchError(400, "frequency must be once or every")
        if starts_at.tzinfo is None or ends_at.tzinfo is None:
            raise WorkbenchError(400, "datetime must include a timezone offset")
        if ends_at <= starts_at:
            raise WorkbenchError(400, "end time must be after the start time")
        if announcement_id:
            row = self.session.get(Announcement, announcement_id)
            if row is None:
                raise WorkbenchError(404, "announcement not found")
            before = AnnouncementView.render(row, self.now)
        else:
            row = Announcement(
                id=str(uuid4()),
                title=heading,
                body=text,
                button_text=button,
                jump_label=None,
                starts_at=starts_at,
                ends_at=ends_at,
                frequency=frequency,
                online=online,
                created_at=self.now,
            )
            self.session.add(row)
            before = None
        row.title = heading
        row.body = text
        row.button_text = button
        row.jump_label = (jump_label or "").strip() or None
        row.starts_at = starts_at
        row.ends_at = ends_at
        row.frequency = frequency
        row.online = online
        self.session.flush()
        self.audit.write(
            "announcement",
            row.id,
            "save_announcement",
            before,
            AnnouncementView.render(row, self.now),
        )
        return row

    def set_online(self, announcement_id: str, online: bool) -> Announcement:
        row = self.session.get(Announcement, announcement_id)
        if row is None:
            raise WorkbenchError(404, "announcement not found")
        before = AnnouncementView.render(row, self.now)
        row.online = online
        self.session.flush()
        self.audit.write(
            "announcement",
            row.id,
            "offline_announcement" if not online else "online_announcement",
            before,
            AnnouncementView.render(row, self.now),
        )
        return row


class AnnouncementView:
    @staticmethod
    def render(row: Announcement, now: datetime) -> dict:
        if not row.online:
            state = "已下线"
        elif row.ends_at <= now:
            state = "已失效"
        elif row.starts_at > now:
            state = "未开始"
        else:
            state = "生效中"
        return {
            "id": row.id,
            "title": row.title,
            "body": row.body,
            "button_text": row.button_text,
            "jump_label": row.jump_label,
            "starts_at": row.starts_at.isoformat(),
            "ends_at": row.ends_at.isoformat(),
            "frequency": row.frequency,
            "online": row.online,
            "state": state,
            "created_at": row.created_at.isoformat(),
        }


class ExampleBook:
    LIMIT = 5

    def __init__(self, session: Session, audit: AuditWriter, now: datetime) -> None:
        self.session = session
        self.audit = audit
        self.now = now

    def list_rows(self) -> list[dict]:
        rows = self.session.scalars(
            select(HomepageExample).order_by(HomepageExample.sort_order, HomepageExample.id)
        ).all()
        return [ExampleView.render(row) for row in rows]

    def add(self, program_name: str, record_id: str | None) -> HomepageExample:
        name = program_name.strip()
        if not name or len(name) > 200:
            raise WorkbenchError(400, "program name is required")
        current = self.session.scalar(select(func.count()).select_from(HomepageExample)) or 0
        if current >= ExampleBook.LIMIT:
            raise WorkbenchError(409, "homepage examples are limited to 5")
        hits, detected = self._facts(name, record_id)
        row = HomepageExample(
            id=str(uuid4()),
            program_name=name,
            record_id=(record_id or "").strip() or None,
            sort_order=int(current) + 1,
            online=True,
            platform_hits=hits,
            detected_on=detected,
        )
        self.session.add(row)
        self.session.flush()
        self.audit.write(
            "homepage_example",
            row.id,
            "add_example",
            None,
            ExampleView.render(row),
        )
        return row

    def set_online(self, example_id: str, online: bool) -> HomepageExample:
        row = self._require(example_id)
        before = ExampleView.render(row)
        row.online = online
        self.session.flush()
        self.audit.write(
            "homepage_example",
            row.id,
            "online_example" if online else "offline_example",
            before,
            ExampleView.render(row),
        )
        return row

    def move(self, example_id: str, direction: str) -> HomepageExample:
        row = self._require(example_id)
        rows = list(
            self.session.scalars(
                select(HomepageExample).order_by(HomepageExample.sort_order, HomepageExample.id)
            ).all()
        )
        index = next(pos for pos, item in enumerate(rows) if item.id == row.id)
        swap_with = index - 1 if direction == "up" else index + 1
        if direction not in {"up", "down"} or swap_with < 0 or swap_with >= len(rows):
            raise WorkbenchError(409, "example cannot move that way")
        other = rows[swap_with]
        before = ExampleView.render(row)
        row.sort_order, other.sort_order = other.sort_order, row.sort_order
        self.session.flush()
        self.audit.write(
            "homepage_example",
            row.id,
            "move_example",
            before,
            ExampleView.render(row),
        )
        return row

    def _require(self, example_id: str) -> HomepageExample:
        row = self.session.get(HomepageExample, example_id)
        if row is None:
            raise WorkbenchError(404, "example not found")
        return row

    def _facts(self, program_name: str, record_id: str | None) -> tuple[int, datetime | None]:
        records = list(
            self.session.scalars(
                select(IndexRecord).where(IndexRecord.program_name == program_name)
            ).all()
        )
        if record_id:
            chosen = self.session.get(IndexRecord, record_id)
            if chosen is not None and chosen not in records:
                records.append(chosen)
        platforms: set[str] = set()
        detected = None
        for record in records:
            task = self.session.get(Task, record.task_id)
            if task is not None and task.platform_name and not task.source_unverified:
                platforms.add(task.platform_name)
            when = None if task is None else task.created_at
            if when is not None and (detected is None or when > detected):
                detected = when
        return len(platforms), detected


class ExampleView:
    @staticmethod
    def render(row: HomepageExample) -> dict:
        return {
            "id": row.id,
            "program_name": row.program_name,
            "record_id": row.record_id,
            "sort_order": row.sort_order,
            "online": row.online,
            "platform_hits": row.platform_hits,
            "detected_on": row.detected_on.isoformat() if row.detected_on else None,
        }


class ProgramCatalog:
    @staticmethod
    def list_programs(session: Session) -> list[dict]:
        records = list(
            session.scalars(select(IndexRecord).order_by(IndexRecord.program_name)).all()
        )
        grouped: dict[str, list[IndexRecord]] = {}
        for record in records:
            grouped.setdefault(record.program_name, []).append(record)
        listed = []
        for name in sorted(grouped):
            sample = grouped[name][0]
            listed.append(
                {
                    "program_name": name,
                    "record_id": sample.id,
                    "records": len(grouped[name]),
                }
            )
        return listed
