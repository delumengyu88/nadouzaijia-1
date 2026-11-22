from nadou.text import TextNormalizer


class ProgramNormalizer:
    """One program per folded title; no title remapping or short-title heuristics."""

    @staticmethod
    def canonical(raw: str) -> str:
        return TextNormalizer.fold(raw)

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
