from __future__ import annotations

from typing import Any, Protocol


class CustomerTagProjectionPort(Protocol):
    """Public owner port for the local contact-tag read model."""

    def save_channel_snapshot_dbapi(
        self,
        executor: Any,
        *,
        unionid: str,
        owner_userid: str,
        tag_ids: list[str],
        tag_names: dict[str, str],
    ) -> dict[str, int]: ...


def build_customer_tag_projection_port() -> CustomerTagProjectionPort:
    from .projection_repository import PostgresCustomerTagProjectionRepository

    return PostgresCustomerTagProjectionRepository()


__all__ = ["CustomerTagProjectionPort", "build_customer_tag_projection_port"]
