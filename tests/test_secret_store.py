from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from aicrm_next.platform.shared import secret_store
from aicrm_next.platform.shared.secret_store import (
    FileSecretStore,
    SecretReference,
    SecretStoreError,
    parse_secret_reference,
)


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def _version_path(root: Path, reference: str) -> Path:
    parsed = parse_secret_reference(reference)
    return root / parsed.key / parsed.version


def test_secret_store_round_trip_uses_strict_modes_and_opaque_reference(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    store = FileSecretStore(root)

    reference = store.write("WECOM_SECRET", "complete-secret-value")
    parsed = parse_secret_reference(reference)

    assert parsed == SecretReference(key="WECOM_SECRET", version=parsed.version)
    assert reference.startswith("secretref:file:WECOM_SECRET:v1_")
    assert "complete-secret-value" not in reference
    assert store.read(reference) == "complete-secret-value"
    assert _mode(root) == 0o700
    assert _mode(root / "WECOM_SECRET") == 0o700
    assert _mode(_version_path(root, reference)) == 0o600


@pytest.mark.parametrize(
    "key",
    ["", "../WECOM_SECRET", "WECOM/SECRET", "wecom-secret", ".WECOM_SECRET", "WECOM SECRET"],
)
def test_secret_store_rejects_unsafe_keys(tmp_path: Path, key: str) -> None:
    with pytest.raises(SecretStoreError, match="secret key"):
        FileSecretStore(tmp_path / "secrets").write(key, "value")


def test_secret_store_requires_absolute_root() -> None:
    with pytest.raises(SecretStoreError, match="root must be absolute"):
        FileSecretStore("relative/secrets")


@pytest.mark.parametrize(
    "reference",
    [
        "",
        "secretref:env:WECOM_SECRET:v1_abc",
        "secretref:file:../WECOM_SECRET:v1_abc",
        "secretref:file:WECOM_SECRET:../v1_abc",
        "secretref:file:WECOM_SECRET:v1_abc/other",
        "secretref:file:WECOM_SECRET",
    ],
)
def test_secret_reference_parser_rejects_tampering(reference: str) -> None:
    with pytest.raises(SecretStoreError, match="secret reference"):
        parse_secret_reference(reference)


def test_secret_store_rotation_is_idempotent_and_previous_version_remains_readable(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path / "secrets")
    first = store.write("WECOM_SECRET", "secret-v1")

    unchanged = store.write("WECOM_SECRET", "secret-v1", current_reference=first)
    second = store.write("WECOM_SECRET", "secret-v2", current_reference=first)

    assert unchanged == first
    assert second != first
    assert store.read(first) == "secret-v1"
    assert store.read(second) == "secret-v2"
    assert len(list((tmp_path / "secrets" / "WECOM_SECRET").iterdir())) == 2


def test_secret_store_rejects_reference_for_a_different_key(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path / "secrets")
    reference = store.write("WECOM_SECRET", "secret-v1")

    with pytest.raises(SecretStoreError, match="does not match key"):
        store.write("WECOM_CONTACT_SECRET", "secret-v2", current_reference=reference)


def test_secret_store_uses_constant_time_comparison_for_idempotency(monkeypatch, tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path / "secrets")
    reference = store.write("WECOM_SECRET", "secret-v1")
    calls: list[tuple[bytes, bytes]] = []
    original = secret_store.hmac.compare_digest

    def capture(left: bytes, right: bytes) -> bool:
        calls.append((left, right))
        return original(left, right)

    monkeypatch.setattr(secret_store.hmac, "compare_digest", capture)

    assert store.matches(reference, "secret-v1") is True
    assert calls == [(b"secret-v1", b"secret-v1")]


def test_secret_store_rejects_wrong_directory_and_file_modes(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(mode=0o755)
    os.chmod(root, 0o755)
    with pytest.raises(SecretStoreError, match="mode 0700"):
        FileSecretStore(root).write("WECOM_SECRET", "secret")

    os.chmod(root, 0o700)
    store = FileSecretStore(root)
    reference = store.write("WECOM_SECRET", "secret")
    version_path = _version_path(root, reference)
    os.chmod(version_path, 0o644)
    with pytest.raises(SecretStoreError, match="mode 0600"):
        store.read(reference)


def test_secret_store_rejects_symlink_escape_for_key_and_version(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    root.mkdir(mode=0o700)
    os.chmod(root, 0o700)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    os.chmod(outside, 0o700)
    (root / "WECOM_SECRET").symlink_to(outside, target_is_directory=True)

    with pytest.raises(SecretStoreError, match="symlink|directory"):
        FileSecretStore(root).read("secretref:file:WECOM_SECRET:v1_0000000000000000_0123456789abcdef")

    (root / "WECOM_SECRET").unlink()
    store = FileSecretStore(root)
    reference = store.write("WECOM_SECRET", "secret")
    version_path = _version_path(root, reference)
    version_path.unlink()
    version_path.symlink_to(tmp_path / "outside-secret")
    with pytest.raises(SecretStoreError, match="symlink|open"):
        store.read(reference)


def test_secret_store_cleans_partial_temp_file_when_publish_fails(monkeypatch, tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    store = FileSecretStore(root)

    def fail_link(*_args, **_kwargs) -> None:
        raise OSError("injected publish failure")

    monkeypatch.setattr(secret_store.os, "link", fail_link)

    with pytest.raises(SecretStoreError, match="publish"):
        store.write("WECOM_SECRET", "complete-secret")

    assert list((root / "WECOM_SECRET").iterdir()) == []


def test_secret_store_fails_closed_for_missing_or_unreadable_reference(tmp_path: Path) -> None:
    store = FileSecretStore(tmp_path / "secrets")

    with pytest.raises(SecretStoreError, match="open"):
        store.read("secretref:file:WECOM_SECRET:v1_0000000000000000_0123456789abcdef")
    with pytest.raises(SecretStoreError, match="must not be empty"):
        store.write("WECOM_SECRET", "")


def test_secret_store_inventory_validates_every_version_and_dangling_root_symlink(tmp_path: Path) -> None:
    root = tmp_path / "secrets"
    store = FileSecretStore(root)
    first = store.write("WECOM_SECRET", "secret-v1")
    second = store.write("WECOM_SECRET", "secret-v2", current_reference=first)

    assert store.list_references() == [first, second]

    replacement = tmp_path / "dangling-secrets"
    root.rename(tmp_path / "real-secrets")
    root.symlink_to(replacement, target_is_directory=True)
    with pytest.raises(SecretStoreError, match="symlink|directory"):
        store.list_references()
