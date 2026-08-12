from typing import Any

from pydantic import BaseModel


class ApiError(BaseModel):
    """API error response model."""

    status: int
    errorId: str
    debugMessage: str | None = None


class ApiErrorException(Exception):
    """Exception that carries an API error response."""

    def __init__(self, error: dict[str, Any]) -> None:
        self.error = ApiError(**error)
        super().__init__(self.error.debugMessage)
