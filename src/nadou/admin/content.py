from datetime import datetime
from uuid import uuid4

from sqlalchemy import false, func, select, text, update
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.models import Announcement, HomepageExample, SplashConfig
from nadou.normalize import ProgramNormalizer
from nadou.public_home import PublicHome


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
    LIMIT = PublicHome.EXAMPLE_LIMIT

    def __init__(self, session: Session, audit: AuditWriter, now: datetime) -> None:
        self.session = session
        self.audit = audit
        self.now = now

    def list_rows(self) -> list[dict]:
        rows = self.session.scalars(
            select(HomepageExample).order_by(HomepageExample.sort_order, HomepageExample.id)
        ).all()
        groups, tokens = PublicHome.example_records(
            self.session, self.now, [row.program_name for row in rows]
        )
        visible = {row.id for row, _ in PublicHome.published_rows(rows, groups, tokens)}
        result = []
        for row in rows:
            pairs = groups.get(ProgramNormalizer.canonical(row.program_name), [])
            selected = [
                (rec, task) for rec, task in pairs if not row.record_id or rec.id == row.record_id
            ]
            result.append(
                {
                    **ExampleView.render(row),
                    **self._facts(selected),
                    "eligible": PublicHome.example_eligible(pairs, row.record_id),
                    "visible": row.id in visible,
                }
            )
        return result

    def describe(self, row: HomepageExample) -> dict:
        return next(item for item in self.list_rows() if item["id"] == row.id)

    def _lock(self) -> None:
        # Serialize both the capacity check and insertion, including an empty publication list.
        if self.session.get_bind().dialect.name == "postgresql":
            self.session.execute(text("LOCK TABLE homepage_examples IN SHARE ROW EXCLUSIVE MODE"))
        else:
            self.session.execute(update(HomepageExample).where(false()).values(online=False))

    def _check_space(self) -> None:
        count = self.session.scalar(
            select(func.count())
            .select_from(HomepageExample)
            .where(HomepageExample.online.is_(True))
        )
        if count >= self.LIMIT:
            raise WorkbenchError(409, "已配置 5 条上线示例，请先将一条下线。")

    def _check_title(self, name: str, excluding: str | None = None) -> None:
        existing = self.session.execute(
            select(HomepageExample.id, HomepageExample.program_name).where(
                HomepageExample.online.is_(True)
            )
        )
        canonical = ProgramNormalizer.canonical(name)
        if any(
            other_id != excluding and ProgramNormalizer.canonical(other) == canonical
            for other_id, other in existing
        ):
            raise WorkbenchError(409, "该节目已有一条上线配置，请先处理原配置。")

    def _qualify(self, name, record_id):
        groups, tokens = PublicHome.example_records(self.session, self.now, [name])
        pairs = groups.get(ProgramNormalizer.canonical(name), [])
        if not PublicHome.example_eligible(pairs, record_id):
            raise WorkbenchError(409, "所选记录当前没有可公开展示的示例授权。")
        if tokens:
            if not record_id and len(pairs) != 1:
                raise WorkbenchError(409, "该节目有多条可公开记录，请先下线旧配置并选择具体记录。")
            record_id = record_id or pairs[0][0].id
            pairs = [(record, task) for record, task in pairs if record.id == record_id]
        return pairs, record_id, tokens.get(record_id)

    def add(self, program_name: str, record_id: str | None) -> HomepageExample:
        name = program_name.strip()
        if not name or len(name) > 200:
            raise WorkbenchError(400, "program name is required")
        self._lock()
        record_id = (record_id or "").strip() or None
        pairs, record_id, token = self._qualify(name, record_id)
        self._check_space()
        self._check_title(name)
        last = self.session.scalar(select(func.max(HomepageExample.sort_order))) or 0
        facts = self._facts(pairs)
        row = HomepageExample(
            id=str(uuid4()),
            program_name=name,
            record_id=(record_id or "").strip() or None,
            publication_token=token,
            sort_order=last + 1,
            online=True,
            platform_hits=facts["platform_hits"],
            detected_on=facts["detected_on"],
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
        self._lock()
        row = self._require(example_id)
        before = ExampleView.render(row)
        if online:
            _, row.record_id, row.publication_token = self._qualify(row.program_name, row.record_id)
            if not row.online:
                self._check_space()
            self._check_title(row.program_name, excluding=row.id)
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
        self._lock()
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

    def _facts(self, pairs) -> dict:
        keys = {key for _, task in pairs if (key := PublicHome.countable_platform(task))}
        latest = max(
            (
                when
                for record, task in pairs
                if (when := PublicHome._hit_time(task, record, self.now))
            ),
            default=None,
        )
        return {"platform_hits": len(keys), "detected_on": latest}


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
    def list_programs(session: Session, now: datetime) -> list[dict]:
        groups, _ = PublicHome.example_records(session, now)
        return [
            {"program_name": name, "record_id": rec.id, "records": len(pairs)}
            for name, pairs in groups.items()
            for rec, _ in pairs
        ]
