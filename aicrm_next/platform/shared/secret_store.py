from __future__ import annotations

import errno
import hmac
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Final


SECRET_REFERENCE_PREFIX: Final = "secretref:file:"
SECRET_REFERENCE_CUTOVER_KEY: Final = "AICRM_SECRET_REFERENCE_CUTOVER"
SECRET_STORE_DIR_KEY: Final = "AICRM_SECRET_STORE_DIR"
MAX_SECRET_BYTES: Final = 64 * 1024
SENSITIVE_SETTING_KEYS: Final = frozenset(
    {
        "SECRET_KEY",
        "AICRM_NEXT_ACTION_TOKEN_SECRET",
        "AICRM_QUESTIONNAIRE_OAUTH_STATE_SECRET",
        "AICRM_AI_AUDIENCE_AGENT_API_KEY",
        "AICRM_RUNTIME_V2_AGENT_API_KEY",
        "AICRM_AUTH_SESSION_HASH_PEPPER",
        "AICRM_AUTH_JWT_SIGNING_KEY",
        "AICRM_HUANGYOUCAN_DB_PASSWORD",
        "AICRM_HXC_CARD_TARGET_VALIDATION_TOKEN",
        "AICRM_MEDIA_UPLOAD_PROVIDER_SECRET",
        "AICRM_OAUTH_IDENTITY_APP_SECRET",
        "AICRM_PAYMENT_COMMERCE_PROVIDER_SECRET",
        "AICRM_WECOM_CONTACT_CALLBACK_AES_KEY",
        "AICRM_WECOM_CONTACT_CALLBACK_TOKEN",
        "AICRM_WECOM_GROUP_SECRET",
        "AICRM_WECOM_MEDIA_SECRET",
        "AICRM_WECOM_OPERATION_MEMBERS_SECRET",
        "AICRM_WECOM_TAG_AGENT_SECRET",
        "DEEPSEEK_API_KEY",
        "AICRM_SIDEBAR_JSSDK_SECRET",
        "SIDEBAR_THIRD_PARTY_API_TOKEN",
        "WECOM_CONTACT_SECRET",
        "WECOM_SECRET",
        "WECOM_ARCHIVE_SECRET",
        "WECOM_CALLBACK_TOKEN",
        "WECOM_CALLBACK_AES_KEY",
        "WECHAT_MP_APP_SECRET",
        "WECHAT_PAY_API_V3_KEY",
        "WECHAT_PAY_CERT_SERIAL_NO",
        "WECHAT_SHOP_APPSECRET",
        "WECHAT_SHOP_CALLBACK_TOKEN",
    }
)

_KEY_PATTERN = re.compile(r"[A-Z][A-Z0-9_]{2,127}\Z")
_VERSION_PATTERN = re.compile(r"v1_[0-9a-f]{16}_[0-9a-f]{16}\Z")
_REFERENCE_PATTERN = re.compile(r"secretref:file:(?P<key>[A-Z][A-Z0-9_]{2,127}):(?P<version>v1_[0-9a-f]{16}_[0-9a-f]{16})\Z")


class SecretStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class SecretReference:
    key: str
    version: str

    def encode(self) -> str:
        return f"{SECRET_REFERENCE_PREFIX}{self.key}:{self.version}"


def _validated_key(key: str) -> str:
    normalized = str(key or "").strip()
    if not _KEY_PATTERN.fullmatch(normalized):
        raise SecretStoreError("secret key is invalid")
    return normalized


def parse_secret_reference(reference: str) -> SecretReference:
    match = _REFERENCE_PATTERN.fullmatch(str(reference or "").strip())
    if not match:
        raise SecretStoreError("secret reference is invalid")
    return SecretReference(key=match.group("key"), version=match.group("version"))


def is_secret_reference(value: object) -> bool:
    return str(value or "").strip().startswith(SECRET_REFERENCE_PREFIX)


class FileSecretStore:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        raw_root = str(root or "").strip()
        if not raw_root:
            raise SecretStoreError("secret store root is required")
        self.root = Path(raw_root).expanduser()
        if not self.root.is_absolute():
            raise SecretStoreError("secret store root must be absolute")

    @classmethod
    def from_environment(cls) -> "FileSecretStore":
        return cls(os.getenv(SECRET_STORE_DIR_KEY, ""))

    def write(self, key: str, value: str, *, current_reference: str = "") -> str:
        normalized_key = _validated_key(key)
        secret_bytes = str(value if value is not None else "").encode("utf-8")
        if not secret_bytes:
            raise SecretStoreError("secret value must not be empty")
        if len(secret_bytes) > MAX_SECRET_BYTES:
            raise SecretStoreError("secret value exceeds maximum size")

        current = str(current_reference or "").strip()
        if current:
            parsed_current = parse_secret_reference(current)
            if parsed_current.key != normalized_key:
                raise SecretStoreError("secret reference does not match key")
            if self.matches(current, value):
                return current

        self._ensure_directory(self.root, expected_mode=0o700)
        key_dir = self.root / normalized_key
        self._ensure_directory(key_dir, expected_mode=0o700)
        version = self._new_version()
        key_fd = self._open_directory(key_dir, expected_mode=0o700)
        temp_name = f".tmp_{secrets.token_hex(16)}"
        temp_created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            file_fd = os.open(temp_name, flags, 0o600, dir_fd=key_fd)
            temp_created = True
            try:
                os.fchmod(file_fd, 0o600)
                self._write_all(file_fd, secret_bytes)
                os.fsync(file_fd)
            finally:
                os.close(file_fd)
            try:
                os.link(
                    temp_name,
                    version,
                    src_dir_fd=key_fd,
                    dst_dir_fd=key_fd,
                    follow_symlinks=False,
                )
            except OSError as exc:
                raise SecretStoreError("secret version publish failed") from exc
            os.unlink(temp_name, dir_fd=key_fd)
            temp_created = False
            os.fsync(key_fd)
        except SecretStoreError:
            raise
        except OSError as exc:
            raise SecretStoreError("secret version write failed") from exc
        finally:
            if temp_created:
                try:
                    os.unlink(temp_name, dir_fd=key_fd)
                except OSError:
                    pass
            os.close(key_fd)

        reference = SecretReference(key=normalized_key, version=version).encode()
        if not self.matches(reference, value):
            raise SecretStoreError("secret version verification failed")
        return reference

    def read(self, reference: str) -> str:
        parsed = parse_secret_reference(reference)
        self._validate_directory(self.root, expected_mode=0o700)
        key_dir = self.root / parsed.key
        self._validate_directory(key_dir, expected_mode=0o700)
        key_fd = self._open_directory(key_dir, expected_mode=0o700)
        try:
            flags = os.O_RDONLY
            if hasattr(os, "O_NOFOLLOW"):
                flags |= os.O_NOFOLLOW
            try:
                file_fd = os.open(parsed.version, flags, dir_fd=key_fd)
            except OSError as exc:
                detail = "symlink" if exc.errno == errno.ELOOP else "open"
                raise SecretStoreError(f"secret version {detail} failed") from exc
            try:
                metadata = os.fstat(file_fd)
                self._validate_metadata(metadata, expected_mode=0o600, kind="file")
                if not stat.S_ISREG(metadata.st_mode):
                    raise SecretStoreError("secret version is not a regular file")
                if metadata.st_size > MAX_SECRET_BYTES:
                    raise SecretStoreError("secret version exceeds maximum size")
                data = self._read_all(file_fd)
            finally:
                os.close(file_fd)
        finally:
            os.close(key_fd)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SecretStoreError("secret version is not valid UTF-8") from exc

    def matches(self, reference: str, candidate: str) -> bool:
        stored = self.read(reference).encode("utf-8")
        provided = str(candidate if candidate is not None else "").encode("utf-8")
        return hmac.compare_digest(stored, provided)

    def list_references(self) -> list[str]:
        try:
            self.root.lstat()
        except FileNotFoundError:
            return []
        except OSError as exc:
            raise SecretStoreError("secret store inventory failed") from exc
        self._validate_directory(self.root, expected_mode=0o700)
        references: list[str] = []
        try:
            key_directories = sorted(self.root.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise SecretStoreError("secret store inventory failed") from exc
        for key_directory in key_directories:
            key = _validated_key(key_directory.name)
            self._validate_directory(key_directory, expected_mode=0o700)
            try:
                versions = sorted(key_directory.iterdir(), key=lambda path: path.name)
            except OSError as exc:
                raise SecretStoreError("secret version inventory failed") from exc
            for version_path in versions:
                if not _VERSION_PATTERN.fullmatch(version_path.name):
                    raise SecretStoreError("secret store contains an unexpected version entry")
                reference = SecretReference(key=key, version=version_path.name).encode()
                self.read(reference)
                references.append(reference)
        return references

    @staticmethod
    def _new_version() -> str:
        return f"v1_{time.time_ns():016x}_{secrets.token_hex(8)}"

    @staticmethod
    def _write_all(file_fd: int, data: bytes) -> None:
        offset = 0
        while offset < len(data):
            written = os.write(file_fd, data[offset:])
            if written <= 0:
                raise SecretStoreError("secret version write was incomplete")
            offset += written

    @staticmethod
    def _read_all(file_fd: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(file_fd, min(8192, MAX_SECRET_BYTES + 1 - total))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_SECRET_BYTES:
                raise SecretStoreError("secret version exceeds maximum size")

    @classmethod
    def _ensure_directory(cls, path: Path, *, expected_mode: int) -> None:
        try:
            path.mkdir(mode=expected_mode, parents=True, exist_ok=False)
            os.chmod(path, expected_mode, follow_symlinks=False)
        except FileExistsError:
            pass
        except OSError as exc:
            raise SecretStoreError("secret directory creation failed") from exc
        cls._validate_directory(path, expected_mode=expected_mode)

    @classmethod
    def _validate_directory(cls, path: Path, *, expected_mode: int) -> None:
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise SecretStoreError("secret directory open failed") from exc
        if stat.S_ISLNK(metadata.st_mode):
            raise SecretStoreError("secret directory must not be a symlink")
        if not stat.S_ISDIR(metadata.st_mode):
            raise SecretStoreError("secret directory is invalid")
        cls._validate_metadata(metadata, expected_mode=expected_mode, kind="directory")

    @classmethod
    def _open_directory(cls, path: Path, *, expected_mode: int) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_DIRECTORY"):
            flags |= os.O_DIRECTORY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            directory_fd = os.open(path, flags)
        except OSError as exc:
            raise SecretStoreError("secret directory open failed") from exc
        try:
            metadata = os.fstat(directory_fd)
            if not stat.S_ISDIR(metadata.st_mode):
                raise SecretStoreError("secret directory is invalid")
            cls._validate_metadata(metadata, expected_mode=expected_mode, kind="directory")
        except Exception:
            os.close(directory_fd)
            raise
        return directory_fd

    @staticmethod
    def _validate_metadata(metadata: os.stat_result, *, expected_mode: int, kind: str) -> None:
        actual_mode = stat.S_IMODE(metadata.st_mode)
        if actual_mode != expected_mode:
            raise SecretStoreError(f"secret {kind} must use mode {expected_mode:04o}")
        if hasattr(os, "geteuid") and metadata.st_uid != os.geteuid():
            raise SecretStoreError(f"secret {kind} must be owned by the runtime user")
