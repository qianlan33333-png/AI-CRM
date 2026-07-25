from __future__ import annotations

from .identity_contact.write_port import IdentityWritePort
from .identity_contact.write_repository import PostgresIdentityWriteRepository


def build_identity_write_port() -> IdentityWritePort:
    return PostgresIdentityWriteRepository()


__all__ = ["build_identity_write_port"]
