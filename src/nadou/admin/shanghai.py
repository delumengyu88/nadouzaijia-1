from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")


class ShanghaiDay:
    """A workbench calendar day is midnight to the next midnight in Asia/Shanghai."""

    @staticmethod
    def parse(day: str) -> datetime:
        text = day.strip()
        if len(text) != 10 or text[4] != "-" or text[7] != "-":
            raise ValueError("date must be YYYY-MM-DD")
        year, month, day_number = int(text[0:4]), int(text[5:7]), int(text[8:10])
        try:
            return datetime(year, month, day_number, tzinfo=SHANGHAI)
        except ValueError as exc:
            raise ValueError("date must be YYYY-MM-DD") from exc

    @staticmethod
    def bounds(start: str | None, end: str | None) -> tuple[datetime | None, datetime | None]:
        opened = ShanghaiDay.parse(start) if start else None
        closed = ShanghaiDay.parse(end) + timedelta(days=1) if end else None
        return opened, closed
