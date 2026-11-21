from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from nadou.models import ProgramAlias
from nadou.text import TextNormalizer


class ProgramNormalizer:
    """Fold a program name and apply the admin alias table. Does not pick among several."""

    def __init__(
        self,
        aliases: Mapping[str, str] | None = None,
        *,
        pairs: list[tuple[str, str]] | None = None,
    ) -> None:
        grouped: dict[str, list[str]] = {}
        items: list[tuple[str, str]] = []
        if aliases:
            items.extend(aliases.items())
        if pairs:
            items.extend(pairs)
        for alias, canonical in items:
            key = TextNormalizer.fold(alias)
            name = TextNormalizer.fold(canonical)
            if not key or not name:
                continue
            bucket = grouped.setdefault(key, [])
            if name not in bucket:
                bucket.append(name)
        self._aliases = grouped

    @classmethod
    def from_session(cls, session: Session) -> "ProgramNormalizer":
        rows = session.scalars(select(ProgramAlias)).all()
        return cls(pairs=[(row.alias, row.canonical_name) for row in rows])

    def candidates(self, raw: str) -> list[str]:
        folded = TextNormalizer.fold(raw)
        if not folded:
            return []
        mapped = self._aliases.get(folded)
        if not mapped:
            return [folded]
        return list(mapped)

    def canonical(self, raw: str) -> str:
        names = self.candidates(raw)
        if len(names) == 1:
            return names[0]
        return ""

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
