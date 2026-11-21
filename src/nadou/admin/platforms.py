from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.admin.audit import AuditWriter
from nadou.admin.errors import WorkbenchError
from nadou.models import PlatformAlias, Task
from nadou.text import TextNormalizer


@dataclass
class PlatformGroup:
    id: int
    network_type: str | None
    standard_name: str
    aliases: list[str]
    enabled: bool
    modified_by: str | None
    modified_at: datetime | None


class PlatformView:
    @staticmethod
    def render(group: PlatformGroup) -> dict:
        return {
            "id": group.id,
            "network_type": group.network_type,
            "standard_name": group.standard_name,
            "aliases": group.aliases,
            "enabled": group.enabled,
            "modified_by": group.modified_by,
            "modified_at": group.modified_at.isoformat() if group.modified_at else None,
        }


class PlatformCatalog:
    """Operator map from alias to one standard name inside a network type."""

    NETWORKS = frozenset({"公网", "专网"})

    def __init__(self, session: Session, audit: AuditWriter) -> None:
        self.session = session
        self.audit = audit

    def list_groups(self) -> list[dict]:
        rows = list(self.session.scalars(select(PlatformAlias).order_by(PlatformAlias.id)).all())
        grouped: dict[tuple[str | None, str], list[PlatformAlias]] = {}
        for row in rows:
            grouped.setdefault((row.network_type, row.canonical_name), []).append(row)
        return [PlatformView.render(self._group(items)) for items in grouped.values()]

    def create(
        self,
        network_type: str,
        standard_name: str,
        aliases: list[str],
        enabled: bool,
        actor_name: str,
        now: datetime,
    ) -> dict:
        network = self._network(network_type)
        name = TextNormalizer.fold(standard_name)
        if not name:
            raise WorkbenchError(400, "standard name is required")
        wanted = self._fold_aliases([standard_name, *aliases])
        self._ensure_free(network, wanted, owner=None)
        for alias in wanted:
            self.session.add(
                PlatformAlias(
                    alias=alias,
                    canonical_name=name,
                    network_type=network,
                    enabled=enabled,
                    modified_by=actor_name,
                    modified_at=now,
                )
            )
        self.session.flush()
        group = self._group(self._rows(network, name))
        self.audit.write(
            "platform",
            str(group.id),
            "create_platform",
            None,
            PlatformView.render(group),
        )
        return PlatformView.render(group)

    def add_alias(self, platform_id: int, alias: str, actor_name: str, now: datetime) -> dict:
        current = self._require(platform_id)
        folded = TextNormalizer.fold(alias)
        if not folded:
            raise WorkbenchError(400, "alias is required")
        before = PlatformView.render(current)
        self._ensure_free(current.network_type, [folded], owner=current.standard_name)
        if folded not in {current.standard_name, *current.aliases}:
            self.session.add(
                PlatformAlias(
                    alias=folded,
                    canonical_name=current.standard_name,
                    network_type=current.network_type,
                    enabled=current.enabled,
                    modified_by=actor_name,
                    modified_at=now,
                )
            )
        self._touch(current, actor_name, now)
        self.session.flush()
        group = self._require(platform_id)
        self.audit.write(
            "platform",
            str(group.id),
            "add_platform_alias",
            before,
            PlatformView.render(group),
        )
        return PlatformView.render(group)

    def set_enabled(self, platform_id: int, enabled: bool, actor_name: str, now: datetime) -> dict:
        current = self._require(platform_id)
        before = PlatformView.render(current)
        rows = self._rows(current.network_type, current.standard_name)
        for row in rows:
            row.enabled = enabled
            row.modified_by = actor_name
            row.modified_at = now
        self.session.flush()
        group = self._group(rows)
        self.audit.write(
            "platform",
            str(group.id),
            "update_platform",
            before,
            PlatformView.render(group),
        )
        return PlatformView.render(group)

    def backfill(
        self,
        task_id: str,
        network_type: str,
        standard_name: str,
        now: datetime,
    ) -> Task:
        del now
        network = self._network(network_type)
        name = TextNormalizer.fold(standard_name)
        rows = self._rows(network, name)
        if not rows:
            raise WorkbenchError(404, "standard platform not found")
        if not rows[0].enabled:
            raise WorkbenchError(409, "standard platform is disabled")
        task = self.session.get(Task, task_id)
        if task is None:
            raise WorkbenchError(404, "task not found")
        if not task.source_unverified:
            raise WorkbenchError(409, "task source is already verified")
        if task.network_type and task.network_type != network:
            raise WorkbenchError(409, "task network does not match the standard platform")
        before = {
            "platform_name": task.platform_name,
            "source_unverified": task.source_unverified,
            "network_type": task.network_type,
        }
        task.platform_name = name
        task.source_unverified = False
        if task.network_type is None:
            task.network_type = network
        self.session.flush()
        self.audit.write(
            "task",
            task.task_id,
            "backfill_platform",
            before,
            {
                "platform_name": task.platform_name,
                "source_unverified": task.source_unverified,
                "network_type": task.network_type,
                "standard_name": name,
            },
        )
        return task

    def _require(self, platform_id: int) -> PlatformGroup:
        anchor = self.session.get(PlatformAlias, platform_id)
        if anchor is None:
            raise WorkbenchError(404, "standard platform not found")
        rows = self._rows(anchor.network_type, anchor.canonical_name)
        if not rows:
            raise WorkbenchError(404, "standard platform not found")
        return self._group(rows)

    def _rows(self, network: str | None, name: str) -> list[PlatformAlias]:
        stmt = select(PlatformAlias).where(PlatformAlias.canonical_name == name)
        if network is None:
            stmt = stmt.where(PlatformAlias.network_type.is_(None))
        else:
            stmt = stmt.where(PlatformAlias.network_type == network)
        return list(self.session.scalars(stmt.order_by(PlatformAlias.id)).all())

    def _ensure_free(self, network: str | None, aliases: list[str], owner: str | None) -> None:
        for alias in aliases:
            stmt = select(PlatformAlias).where(PlatformAlias.alias == alias)
            if network is None:
                stmt = stmt.where(PlatformAlias.network_type.is_(None))
            else:
                stmt = stmt.where(PlatformAlias.network_type == network)
            existing = self.session.scalar(stmt)
            if existing is None or existing.canonical_name == owner:
                continue
            raise WorkbenchError(
                409,
                {
                    "message": "alias already maps to a standard platform",
                    "alias": alias,
                    "existing_standard_name": existing.canonical_name,
                },
            )

    def _touch(self, group: PlatformGroup, actor_name: str, now: datetime) -> None:
        for row in self._rows(group.network_type, group.standard_name):
            row.modified_by = actor_name
            row.modified_at = now

    @staticmethod
    def _group(rows: list[PlatformAlias]) -> PlatformGroup:
        anchor = next((row for row in rows if row.alias == row.canonical_name), rows[0])
        aliases = sorted(row.alias for row in rows if row.alias != anchor.canonical_name)
        dated = [row for row in rows if row.modified_at is not None]
        latest = max(dated, key=lambda row: row.modified_at) if dated else rows[0]
        return PlatformGroup(
            id=anchor.id,
            network_type=anchor.network_type,
            standard_name=anchor.canonical_name,
            aliases=aliases,
            enabled=anchor.enabled,
            modified_by=latest.modified_by,
            modified_at=latest.modified_at,
        )

    @staticmethod
    def _fold_aliases(values: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for value in values:
            folded = TextNormalizer.fold(value)
            if not folded or folded in seen:
                continue
            if len(folded) > 200:
                raise WorkbenchError(400, "alias is too long")
            seen.add(folded)
            ordered.append(folded)
        if not ordered:
            raise WorkbenchError(400, "standard name is required")
        return ordered

    @staticmethod
    def _network(value: str) -> str:
        if value not in PlatformCatalog.NETWORKS:
            raise WorkbenchError(400, "network type must be 公网 or 专网")
        return value
