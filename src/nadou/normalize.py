from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.models import PlatformAlias, ProgramAlias
from nadou.text import TextNormalizer


class PlatformLabel:
    """Province plus operator is the counted name. Subtype is only a display suffix."""

    TOKENS = ("OTT", "IPTV", "百视通")

    @staticmethod
    def subtype(network: str | None, filename: str | None) -> str | None:
        parts: list[str] = []
        if network:
            parts.append(network)
        text = (filename or "").casefold()
        for token in PlatformLabel.TOKENS:
            if token.casefold() in text:
                parts.append(token)
        if not parts:
            return None
        return " ".join(parts)

    @staticmethod
    def display(name: str | None, subtype: str | None) -> str:
        if not name:
            return "平台未识别"
        if subtype:
            return f"{name} · {subtype}"
        return name


class NormalizedPlatform:
    def __init__(self, name: str | None, unverified: bool) -> None:
        self.name = name
        self.unverified = unverified


class PlatformNormalizer:
    """Fold a platform string, then apply the alias table.

    The same instance is used for the CSV platform column and for parser output.
    """

    def __init__(self, aliases: Mapping[str, str]) -> None:
        self._aliases = {
            TextNormalizer.fold(alias): TextNormalizer.fold(canonical)
            for alias, canonical in aliases.items()
        }

    @classmethod
    def from_session(cls, session: Session) -> "PlatformNormalizer":
        rows = session.scalars(select(PlatformAlias)).all()
        return cls({row.alias: row.canonical_name for row in rows})

    def normalize(self, raw: str | None) -> NormalizedPlatform:
        if raw is None:
            return NormalizedPlatform(None, True)
        folded = TextNormalizer.fold(raw)
        if not folded:
            return NormalizedPlatform(None, True)
        canonical = TextNormalizer.fold(self._aliases.get(folded, folded))
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
