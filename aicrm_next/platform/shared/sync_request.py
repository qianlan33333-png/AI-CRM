from __future__ import annotations

from typing import Any

from anyio import from_thread
from starlette.requests import Request


def read_request_body(request: Request) -> bytes:
    return from_thread.run(request.body)


def read_request_json(request: Request) -> Any:
    return from_thread.run(request.json)
