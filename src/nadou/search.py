from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.entitlements import AccessDecision, AccessGrants, EntitlementPolicy, LockedSummary
from nadou.models import Entitlement, IndexRecord, Task, User
from nadou.normalize import ProgramNormalizer
from nadou.platform_names import PlatformNames
from nadou.program_titles import ProgramTitleGuard
from nadou.schemas import (
    LockedRecordOut,
    LockedSummaryOut,
    OpenRecordOut,
    PlatformGroupOut,
    SearchOut,
    TierCounts,
)
from nadou.signed_url import SignedUrlProvider
from nadou.text import RecordAge


class UnknownUser(Exception):
    pass


class EmptyQuery(Exception):
    pass


class ProgramSearch:
    """Search one program and filter every record for the caller."""

    UNRECOGNIZED_PLATFORM = PlatformNames.UNRECOGNIZED
    HIT_LABEL = "已命中"
    REASON_UNKNOWN_AGE = "检测时间待核实"
    _LOCKED_KEYS = ("record_id", "age_bucket", "locked", "lock_reason")

    def __init__(self, session: Session, urls: SignedUrlProvider, now: datetime) -> None:
        self.session = session
        self.urls = urls
        self.now = now

    def search(self, raw_name: str, user_id: str | None) -> SearchOut:
        canonical = ProgramNormalizer.from_session(self.session).canonical(raw_name)
        if not canonical:
            raise EmptyQuery(raw_name)
        if not ProgramTitleGuard.is_billable(canonical):
            return self._not_billable(canonical)
        grants = self._live_grants(user_id)
        rows = list(
            self.session.scalars(
                select(IndexRecord).where(IndexRecord.program_name == canonical)
            ).all()
        )
        task_ids = {row.task_id for row in rows}
        tasks: dict[str, Task] = {}
        if task_ids:
            for task in self.session.scalars(select(Task).where(Task.task_id.in_(task_ids))).all():
                tasks[task.task_id] = task

        kept: list[tuple[int | None, IndexRecord, Task | None]] = []
        platforms: set[str] = set()
        unverified = 0
        for record in rows:
            task = tasks.get(record.task_id)
            if not self.included(task) or not self.confirmed(record):
                continue
            when = self.record_time(task, record)
            age = RecordAge.days(when, self.now) if when is not None else None
            if age is not None and age >= 30:
                key = self._platform_key(task)
                if key is None:
                    unverified += 1
                else:
                    platforms.add(key)
            kept.append((age, record, task))

        kept.sort(
            key=lambda item: (
                item[0] is None,
                item[0] if item[0] is not None else 0,
                item[1].id,
            )
        )
        open_records: list[OpenRecordOut] = []
        locked_records: list[LockedRecordOut] = []
        locked_ages: list[int] = []
        any_old = False
        any_recent = False
        for age, record, task in kept:
            if age is None:
                locked_records.append(self._unknown_age(record))
                continue
            if age >= 30:
                any_old = True
            else:
                any_recent = True
            decision = EntitlementPolicy.decide(age, self._grants_for(grants, canonical, record.id))
            presented = self._present(record, task, decision, user_id)
            if isinstance(presented, LockedRecordOut):
                if decision.locked:
                    locked_ages.append(age)
                locked_records.append(presented)
            else:
                open_records.append(presented)

        if any_old:
            outcome = "hit"
        elif any_recent:
            outcome = "recent_only"
        else:
            outcome = "miss"
        summary = EntitlementPolicy.summarize_locked(
            locked_ages,
            self._grants_for(grants, canonical, ""),
        )
        return SearchOut(
            program_name=canonical,
            outcome=outcome,
            platform_count=len(platforms),
            unverified_source_count=unverified,
            locked_summary=self._summary_out(summary),
            platform_groups=self._group_by_platform(open_records),
            locked_records=locked_records,
        )

    def _live_grants(self, user_id: str | None) -> list[Entitlement]:
        if user_id is None:
            return []
        user = self.session.get(User, user_id)
        if user is None:
            raise UnknownUser(user_id)
        rows = self.session.scalars(select(Entitlement).where(Entitlement.user_id == user_id)).all()
        return [row for row in rows if row.expires_at > self.now]

    @staticmethod
    def _grants_for(rows: list[Entitlement], program: str, record_id: str) -> AccessGrants:
        vip = regular = program_30 = program_90 = screenshot = False
        for row in rows:
            if row.kind == "vip":
                vip = True
            elif row.kind == "regular":
                regular = True
            elif row.kind == "program_30" and row.program_name == program:
                program_30 = True
            elif row.kind == "program_90" and row.program_name == program:
                program_90 = True
            elif row.kind == "screenshot" and row.record_id == record_id:
                screenshot = True
        return AccessGrants(
            vip=vip,
            regular=regular,
            program_30=program_30,
            program_90=program_90,
            screenshot=screenshot,
        )

    def _not_billable(self, canonical: str) -> SearchOut:
        summary = EntitlementPolicy.summarize_locked([], AccessGrants())
        return SearchOut(
            program_name=canonical,
            outcome="miss",
            platform_count=0,
            unverified_source_count=0,
            locked_summary=self._summary_out(summary),
            platform_groups=[],
            locked_records=[],
        )

    @staticmethod
    def _platform_key(task: Task | None) -> tuple[str, str] | None:
        if task is None or task.source_unverified or not task.platform_operator:
            return None
        return (task.platform_province or "", task.platform_operator)

    @staticmethod
    def confirmed(record: IndexRecord) -> bool:
        """Only a confirmed hit is user-facing. Dispute rows stay internal."""
        return record.hit_type == "consensus"

    @staticmethod
    def included(task: Task | None) -> bool:
        if task is None:
            return True
        if task.is_test:
            return False
        return task.status == "done"

    @staticmethod
    def record_time(task: Task | None, record: IndexRecord) -> datetime | None:
        if task is not None and task.created_at is not None:
            return task.created_at
        if task is not None and task.file_uploaded_at is not None:
            return task.file_uploaded_at
        return record.uploaded_at

    @staticmethod
    def _unverified(task: Task | None) -> bool:
        return task is None or task.source_unverified or not task.platform_name

    @staticmethod
    def _summary_out(summary: LockedSummary) -> LockedSummaryOut:
        return LockedSummaryOut(
            locked_total=summary.locked_total,
            by_required_tier=TierCounts(
                member=summary.member,
                vip=summary.vip,
                evidence_only=summary.evidence_only,
            ),
            unlockable_total=summary.unlockable_total,
            cheapest_full_unlock=summary.cheapest_full_unlock,
            unlocks_by_product=summary.unlocks_by_product,
        )

    @staticmethod
    def _group_by_platform(records: list[OpenRecordOut]) -> list[PlatformGroupOut]:
        buckets: dict[tuple[str, str, str], list[OpenRecordOut]] = {}
        for record in records:
            key = (
                record.platform or "",
                record.platform_province or "",
                record.platform_operator or "",
            )
            buckets.setdefault(key, []).append(record)
        groups: list[PlatformGroupOut] = []
        for platform, province, operator in sorted(buckets):
            groups.append(
                PlatformGroupOut(
                    platform=platform or ProgramSearch.UNRECOGNIZED_PLATFORM,
                    platform_province=province or None,
                    platform_operator=operator or None,
                    records=buckets[(platform, province, operator)],
                )
            )
        return groups

    @staticmethod
    def _unknown_age(record: IndexRecord) -> LockedRecordOut:
        return LockedRecordOut(
            record_id=record.id,
            age_bucket="unknown",
            locked=True,
            lock_reason=ProgramSearch.REASON_UNKNOWN_AGE,
        )

    def _present(
        self,
        record: IndexRecord,
        task: Task | None,
        decision: AccessDecision,
        user_id: str | None,
    ) -> LockedRecordOut | OpenRecordOut:
        bucket = decision.age_bucket
        if decision.locked:
            return LockedRecordOut(
                record_id=record.id,
                age_bucket=bucket,
                locked=True,
                lock_reason=decision.lock_reason or "",
            )
        payload: dict = {
            "record_id": record.id,
            "age_bucket": bucket,
            "locked": False,
            "access": decision.access,
            "can_download_report": decision.can_download_report,
            "can_download_screenshot": decision.can_download_screenshot,
        }
        if decision.reveal_fields:
            payload["platform"] = (
                self.UNRECOGNIZED_PLATFORM if self._unverified(task) else task.platform_name
            )
            payload["hit_label"] = ProgramSearch.HIT_LABEL
            if task is not None and not self._unverified(task):
                if task.platform_province:
                    payload["platform_province"] = task.platform_province
                if task.platform_operator:
                    payload["platform_operator"] = task.platform_operator
                if task.platform_subtype:
                    payload["platform_subtype"] = task.platform_subtype
            if task is not None and task.network_type:
                payload["network_type"] = task.network_type
            payload["frame_count"] = record.frame_count
            payload["timestamps"] = list(record.timestamps or [])
            when = self.record_time(task, record)
            if when is not None:
                payload["checked_at"] = when.astimezone(RecordAge.TZ).isoformat()
            if record.category:
                payload["category"] = record.category
        if decision.thumbnail:
            payload["thumbnail_url"] = self._sign(decision.thumbnail, record.id, user_id)
        if decision.hd:
            payload["hd_url"] = self._sign("hd", record.id, user_id)
        return OpenRecordOut(**payload)

    def _sign(self, variant: str, record_id: str, user_id: str | None) -> str:
        url = self.urls.sign_derived(variant, record_id, user_id)
        if not url.startswith("signed://derived/"):
            raise RuntimeError("signer returned a non-derived URL")
        return url
