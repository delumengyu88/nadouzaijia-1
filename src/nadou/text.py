import re
from datetime import datetime
from zoneinfo import ZoneInfo


class TextNormalizer:
    """Trim, collapse whitespace, fold full-width ASCII, and casefold."""

    @staticmethod
    def fold(value: str) -> str:
        chars: list[str] = []
        for char in value:
            code = ord(char)
            if code == 0x3000:
                chars.append(" ")
            elif 0xFF01 <= code <= 0xFF5E:
                chars.append(chr(code - 0xFEE0))
            else:
                chars.append(char)
        text = "".join(chars)
        text = re.sub(r"\s+", " ", text).strip()
        return text.casefold()


class RecordAge:
    """Calendar-day age in Beijing. The boundary rolls at 00:00 local time."""

    TZ = ZoneInfo("Asia/Shanghai")
    SUBMITTED_FORMAT = "%Y-%m-%d %H:%M:%S"

    @staticmethod
    def parse_submitted(text: str) -> datetime:
        try:
            parsed = datetime.strptime(text.strip(), RecordAge.SUBMITTED_FORMAT)
        except ValueError as exc:
            raise ValueError(f"bad submitted time {text!r}") from exc
        return parsed.replace(tzinfo=RecordAge.TZ)

    @staticmethod
    def days(when: datetime, now: datetime) -> int:
        if when.tzinfo is None or now.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        created = when.astimezone(RecordAge.TZ).date()
        today = now.astimezone(RecordAge.TZ).date()
        return (today - created).days
