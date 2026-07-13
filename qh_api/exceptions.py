from __future__ import annotations

from typing import Any


class QHAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None, response_body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class QHAuthError(QHAPIError):
    pass


class QHRateLimitError(QHAPIError):
    pass
