import json
from functools import lru_cache
from pathlib import Path

from nadou.config import get_settings
from nadou.text import TextNormalizer


@lru_cache
def load_stop_words(path: str) -> frozenset[str]:
    source = Path(path) if path else Path(__file__).with_name("stop_words.json")
    words = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(words, list) or any(not isinstance(word, str) for word in words):
        raise ValueError("Stop words must be a JSON array of strings")
    return frozenset(TextNormalizer.fold(word) for word in words if TextNormalizer.fold(word))


class ProgramTitleGuard:
    """Block only normalized whole words; defaults to the approved first batch."""

    _stop_words: frozenset[str] | None = None

    @staticmethod
    def stop_words() -> frozenset[str]:
        if ProgramTitleGuard._stop_words is not None:
            return ProgramTitleGuard._stop_words
        return load_stop_words(get_settings().stop_words_path)

    @staticmethod
    def set_stop_words(words: frozenset[str] | None) -> None:
        # Explicit overrides support isolated tests; None restores configured defaults.
        ProgramTitleGuard._stop_words = (
            None if words is None else frozenset(TextNormalizer.fold(word) for word in words)
        )

    @staticmethod
    def is_billable(title: str, *, stop_words: frozenset[str] | None = None) -> bool:
        folded = TextNormalizer.fold(title)
        if not folded:
            return False
        words = ProgramTitleGuard.stop_words() if stop_words is None else stop_words
        return folded not in {TextNormalizer.fold(word) for word in words}
