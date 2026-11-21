from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.models import PlatformAlias, ProgramAlias
from nadou.text import TextNormalizer


class NormalizedPlatform:
    def __init__(self, name: str | None, unverified: bool) -> None:
        self.name = name
        self.unverified = unverified


class PlatformNormalizer:
    """Fold a platform string, then apply the alias table.

    The same instance is used for the CSV platform column and for parser output.
    Network-specific aliases win over legacy rows that have no network type.
    Disabled platforms are not loaded from the database.
    """

    def __init__(
        self,
        aliases: Mapping[str, str],
        by_network: Mapping[tuple[str, str], str] | None = None,
    ) -> None:
        self._aliases = {
            TextNormalizer.fold(alias): TextNormalizer.fold(canonical)
            for alias, canonical in aliases.items()
        }
        self._by_network = {
            (network, TextNormalizer.fold(alias)): TextNormalizer.fold(canonical)
            for (network, alias), canonical in (by_network or {}).items()
        }

    @classmethod
    def from_session(cls, session: Session) -> "PlatformNormalizer":
        rows = session.scalars(select(PlatformAlias).where(PlatformAlias.enabled.is_(True))).all()
        generic: dict[str, str] = {}
        by_network: dict[tuple[str, str], str] = {}
        for row in rows:
            if row.network_type:
                by_network[(row.network_type, row.alias)] = row.canonical_name
            else:
                generic[row.alias] = row.canonical_name
        return cls(generic, by_network)

    def normalize(self, raw: str | None, network: str | None = None) -> NormalizedPlatform:
        if raw is None:
            return NormalizedPlatform(None, True)
        folded = TextNormalizer.fold(raw)
        if not folded:
            return NormalizedPlatform(None, True)
        specific = self._by_network.get((network, folded)) if network else None
        chosen = specific if specific is not None else self._aliases.get(folded, folded)
        canonical = TextNormalizer.fold(chosen)
        if not canonical:
            return NormalizedPlatform(None, True)
        return NormalizedPlatform(canonical, False)


class ProgramNormalizer:
    """Fold a program name, apply aliases, and drop duplicates."""

    def __init__(self, aliases: Mapping[str, str]) -> None:
        self._aliases = {
            TextNormalizer.fold(alias): TextNormalizer.fold(canonical)
            for alias, canonical in aliases.items()
        }

    @classmethod
    def from_session(cls, session: Session) -> "ProgramNormalizer":
        rows = session.scalars(select(ProgramAlias)).all()
        return cls({row.alias: row.canonical_name for row in rows})

    def canonical(self, raw: str) -> str:
        folded = TextNormalizer.fold(raw)
        if not folded:
            return ""
        return TextNormalizer.fold(self._aliases.get(folded, folded))

    def dedupe(self, names: list[str]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for name in names:
            canonical = self.canonical(name)
            if not canonical or canonical in seen:
                continue
            seen.add(canonical)
            ordered.append(canonical)
        return ordered
