from datetime import datetime, timedelta

from nadou.text import RecordAge


class WorkingDays:
    """Beijing Monday–Friday. Public holidays are not excluded in this slice."""

    @staticmethod
    def add(start: datetime, days: int) -> datetime:
        cursor = start.astimezone(RecordAge.TZ)
        remaining = days
        while remaining > 0:
            cursor += timedelta(days=1)
            if cursor.weekday() < 5:
                remaining -= 1
        return cursor
