class HD2APIError(Exception):
    """Safe provider failure. Never include response bodies or credentials."""


class HD2UnavailableError(HD2APIError):
    pass


class HD2RateLimitError(HD2APIError):
    def __init__(self, retry_after: float = 10):
        super().__init__("API rate limited")
        self.retry_after = retry_after


class HD2SchemaError(HD2APIError):
    pass


class CommandError(Exception):
    pass
