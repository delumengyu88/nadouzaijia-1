from dataclasses import replace
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.config import get_settings
from nadou.entitlements import AccessDecision, AccessGrants, EntitlementPolicy, LockedSummary
from nadou.importer import TestTaskFilter
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
from nadou.summary_source import approved_summary
from nadou.text import RecordAge


class UnknownUser(Exception):
    pass


class EmptyQuery(Exception):
    pass


class ProgramSearch:
    """Search one program and filter every record for the caller."""

    UNRECOGNIZED_PLATFORM = PlatformNames.OTHER
    HIT_LABEL = "已命中"
    REASON_UNKNOWN_AGE = "检测时间待核实"
    _LOCKED_KEYS = ("record_id", "age_bucket", "locked", "lock_reason")

    def __init__(self, session: Session, urls: SignedUrlProvider | None, now: datetime) -> None:
        self.session = session
        self.urls = urls
        self.now = now

    def search(self, raw_name: str, user_id: str | None) -> SearchOut:
        canonical = ProgramNormalizer().canonical(raw_name)
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
        summary_ids = set()
        for record in rows:
            task = tasks.get(record.task_id)
            if approved_summary(record, task):
                if RecordAge.days(task.created_at, self.now) < 30:
                    continue
                summary_ids.add(record.id)
            elif not self.visible(record, task):
                continue
            when = self.tier_time(task, record)
            age = RecordAge.days(when, self.now) if when is not None else None
            if age is not None and age >= 30:
                key = self._platform_key(task)
                if key is None:
                    unverified += 1
                else:
                    platforms.add(key)
            kept.append((age, record, task))

        # Real dates first, then generated placeholders, then rows with no date.
        kept.sort(
            key=lambda item: (
                item[0] is None,
                self.has_generated_date(item[2], item[1]),
                item[0] if item[0] is not None else 0,
                item[1].id,
            )
        )
        open_records: list[OpenRecordOut] = []
        evidence_platforms: list[str] = []
        locked_records: list[LockedRecordOut] = []
        locked_ages: list[int] = []
        summary_hidden = 0
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
            if record.id in summary_ids:
                if not decision.locked and decision.reveal_fields:
                    open_records.append(
                        OpenRecordOut(
                            record_id=record.id,
                            age_bucket=decision.age_bucket,
                            locked=False,
                            access="summary",
                            summary_only=True,
                            can_download_report=False,
                            can_download_screenshot=False,
                            platform=task.platform_name,
                            network_type=task.network_type,
                            hit_label=self.HIT_LABEL,
                            checked_on=task.created_at.astimezone(RecordAge.TZ).date().isoformat(),
                            frame_count=record.frame_count,
                        )
                    )
                else:
                    summary_hidden += 1
                continue
            presented = self._present(record, task, decision, user_id)
            if isinstance(presented, LockedRecordOut):
                if decision.locked:
                    locked_ages.append(age)
                locked_records.append(presented)
            else:
                open_records.append(presented)
                name = presented.platform
                if (
                    self.reportable(record)
                    and name
                    and name != self.UNRECOGNIZED_PLATFORM
                    and name not in evidence_platforms
                ):
                    evidence_platforms.append(name)

        if any_old:
            outcome = "hit"
        elif any_recent:
            outcome = "recent_only"
        else:
            outcome = self.empty_outcome()
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
            summary_only_count=len(summary_ids) or None,
            summary_hidden_count=summary_hidden if summary_ids else None,
            platform_groups=self._group_by_platform(open_records),
            locked_records=locked_records,
            evidence_platforms=evidence_platforms,
            evidence_program_name=canonical
            if any(
                record.id not in summary_ids and self.reportable(record)
                for _age, record, _task in kept
            )
            else "",
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
            outcome=self.empty_outcome(),
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
    def empty_outcome() -> str:
        # A partial or absent source manifest cannot establish a catalog-wide miss.
        return "miss" if get_settings().demo_data else "unverified"

    @classmethod
    def visible(cls, record: IndexRecord, task: Task | None) -> bool:
        if record.summary_grant is not None or not cls.confirmed(record) or not cls.included(task):
            return False
        if get_settings().demo_data:
            return True
        return (
            task is not None
            and bool(task.approved_hitlist_sha256)
            and record.hitlist_sha256 == task.approved_hitlist_sha256
            and not record.date_generated
            and cls.record_time(task, record) is not None
            and TestTaskFilter.commercial_task(task)
        )

    @staticmethod
    def confirmed(record: IndexRecord) -> bool:
        """Only a confirmed hit is user-facing. Review and dispute rows stay internal."""
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
    def reportable(record: IndexRecord) -> bool:
        """False for rows whose date is a generated placeholder.

        Those rows never enter an xlsx export, 委托取证 prefill, or any formal
        report, whatever date or tier they show in search.
        """
        return not record.date_generated

    @staticmethod
    def has_generated_date(task: Task | None, record: IndexRecord) -> bool:
        """The row's only date is a generated placeholder."""
        return (
            ProgramSearch.record_time(task, record) is None
            and record.date_generated
            and record.generated_uploaded_at is not None
        )

    @staticmethod
    def tier_time(task: Task | None, record: IndexRecord) -> datetime | None:
        """Real detection time, else the generated placeholder. Search only."""
        real = ProgramSearch.record_time(task, record)
        if real is not None:
            return real
        if record.date_generated:
            return record.generated_uploaded_at
        return None

    @staticmethod
    def _unverified(task: Task | None) -> bool:
        return (
            task is None
            or task.source_unverified
            or not (task.platform_name or "").strip()
            or task.platform_name in (PlatformNames.UNRECOGNIZED, PlatformNames.OTHER)
        )

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
        # Unrecognized platforms (shown as 其他平台) always come last.
        for platform, province, operator in sorted(
            buckets, key=lambda key: (key[0] in ("", PlatformNames.OTHER), key)
        ):
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
        if not decision.locked and not self.reportable(record):
            decision = replace(decision, can_download_report=False)
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
            "can_download_screenshot": decision.can_download_screenshot and self.urls is not None,
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
            when = self.tier_time(task, record)
            if when is not None:
                payload["checked_at"] = when.astimezone(RecordAge.TZ).isoformat()
            if record.category:
                payload["category"] = record.category
        if decision.thumbnail and self.urls is not None:
            payload["thumbnail_url"] = self._sign(decision.thumbnail, record.id, user_id)
        if decision.hd and self.urls is not None:
            payload["hd_url"] = self._sign("hd", record.id, user_id)
        return OpenRecordOut(**payload)

    def _sign(self, variant: str, record_id: str, user_id: str | None) -> str:
        url = self.urls.sign_derived(variant, record_id, user_id)
        if not url.startswith("signed://derived/"):
            raise RuntimeError("signer returned a non-derived URL")
        return url
