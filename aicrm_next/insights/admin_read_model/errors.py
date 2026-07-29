from __future__ import annotations


class AdminReadModelError(RuntimeError):
    def __init__(self, message: str, *, error_code: str = "admin_read_model_query_failed") -> None:
        super().__init__(message)
        self.error_code = error_code

