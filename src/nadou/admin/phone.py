class OpenIdMask:
    """Keep a short prefix and suffix. Support reads use this."""

    @staticmethod
    def apply(value: str | None) -> str | None:
        if value is None:
            return None
        if len(value) <= 6:
            return value[:1] + ("*" * max(len(value) - 2, 0)) + value[-1:]
        return value[:3] + ("*" * (len(value) - 6)) + value[-3:]


class PhoneMask:
    """Show only the last four digits. Support reads use this; the full value stays in the DB."""

    @staticmethod
    def apply(phone: str | None) -> str | None:
        if phone is None:
            return None
        if len(phone) <= 4:
            return "*" * len(phone)
        hidden = len(phone) - 4
        return ("*" * hidden) + phone[-4:]
