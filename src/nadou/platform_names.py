from nadou.text import TextNormalizer


class PlatformResolution:
    def __init__(self, name: str | None, unrecognized: bool) -> None:
        self.name = name
        self.unrecognized = unrecognized


class PlatformNames:
    """Developer-maintained platform names. This map is not stored in the database."""

    UNRECOGNIZED = "平台未识别"
    _ENTRIES: tuple[tuple[str, str], ...] = (
        ("gd移动", "广东移动"),
        ("广东移动", "广东移动"),
        ("示例视频", "示例视频"),
        ("示例视频ott", "示例视频"),
        ("示例视频平台", "示例视频"),
        ("示例专网", "示例专网"),
        ("双解平台", "广东移动"),
        ("双解平台", "示例视频"),
    )

    @staticmethod
    def standards(raw: str | None) -> tuple[str, ...]:
        if raw is None:
            return ()
        folded = TextNormalizer.fold(raw)
        if not folded:
            return ()
        found = PlatformNames._table().get(folded)
        if found is None:
            return ()
        return found

    @staticmethod
    def normalize(raw: str | None) -> PlatformResolution:
        """One standard name, or unrecognized when the map has zero or several."""
        names = PlatformNames.standards(raw)
        if len(names) == 1:
            return PlatformResolution(names[0], False)
        return PlatformResolution(None, True)

    @staticmethod
    def _table() -> dict[str, tuple[str, ...]]:
        grouped: dict[str, list[str]] = {}
        for alias, standard in PlatformNames._ENTRIES:
            key = TextNormalizer.fold(alias)
            name = TextNormalizer.fold(standard)
            if not key or not name:
                continue
            bucket = grouped.setdefault(key, [])
            if name not in bucket:
                bucket.append(name)
        return {key: tuple(names) for key, names in grouped.items()}
