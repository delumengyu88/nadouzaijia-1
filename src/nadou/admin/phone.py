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
