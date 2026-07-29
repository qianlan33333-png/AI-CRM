from __future__ import annotations

from .crm.identity_contact.write_port import IdentityWritePort
from .crm.identity_contact.write_repository import PostgresIdentityWriteRepository


def build_identity_write_port() -> IdentityWritePort:
    return PostgresIdentityWriteRepository()


__all__ = ["build_identity_write_port"]
