class WorkbenchError(Exception):
    """A domain failure the API turns into an HTTP response."""

    def __init__(self, status_code: int, detail: str | dict) -> None:
        super().__init__(detail if isinstance(detail, str) else detail.get("message", ""))
        self.status_code = status_code
        self.detail = detail
