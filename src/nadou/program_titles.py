from nadou.text import TextNormalizer


class ProgramTitleGuard:
    """Block OCR fragments and very short titles from becoming billable hits.

    `allow_short` is the later flag that lets a title of two characters or fewer match.
    Stop-words never match, even when that flag is set.
    """

    DEFAULT_STOP_WORDS = frozenset({"家庭", "狗", "怪", "远方"})
    SHORT_MAX = 2

    @staticmethod
    def is_billable(
        title: str,
        *,
        allow_short: bool = False,
        stop_words: frozenset[str] | None = None,
    ) -> bool:
        folded = TextNormalizer.fold(title)
        if not folded:
            return False
        words = ProgramTitleGuard.DEFAULT_STOP_WORDS if stop_words is None else stop_words
        blocked = {TextNormalizer.fold(word) for word in words}
        if folded in blocked:
            return False
        if len(folded) <= ProgramTitleGuard.SHORT_MAX and not allow_short:
            return False
        return True
