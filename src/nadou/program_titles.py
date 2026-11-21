from nadou.text import TextNormalizer


class ProgramTitleGuard:
    """Stop-word hook. The default list is empty; callers pass words in tests or config."""

    _stop_words: frozenset[str] = frozenset()

    @staticmethod
    def stop_words() -> frozenset[str]:
        return ProgramTitleGuard._stop_words

    @staticmethod
    def set_stop_words(words: frozenset[str]) -> None:
        folded = {TextNormalizer.fold(word) for word in words if word}
        ProgramTitleGuard._stop_words = frozenset(folded)

    @staticmethod
    def is_billable(title: str, *, stop_words: frozenset[str] | None = None) -> bool:
        folded = TextNormalizer.fold(title)
        if not folded:
            return False
        words = ProgramTitleGuard._stop_words if stop_words is None else stop_words
        blocked = {TextNormalizer.fold(word) for word in words if word}
        return folded not in blocked
