from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from dataclasses import dataclass


SESSION_PREFIX = "ss_"
CSRF_PREFIX = "csrf_"
CLIENT_SECRET_PREFIX = "aics_"


@dataclass(frozen=True)
class IssuedCredential:
    value: str
    digest: str
    prefix: str


class CredentialHasher:
    def __init__(self, pepper: str | bytes) -> None:
        raw = pepper.encode("utf-8") if isinstance(pepper, str) else bytes(pepper)
        if len(raw) < 32:
            raise ValueError("credential hashing pepper must contain at least 32 bytes")
        self._pepper = raw

    def issue(self, prefix: str, *, entropy_bytes: int = 32) -> IssuedCredential:
        if prefix not in {SESSION_PREFIX, CSRF_PREFIX}:
            raise ValueError("unsupported server credential prefix")
        if entropy_bytes < 32:
            raise ValueError("server credentials require at least 256 bits of entropy")
        value = prefix + _random_text(entropy_bytes)
        return IssuedCredential(value=value, digest=self.digest(value), prefix=prefix)

    def digest(self, credential: str) -> str:
        value = str(credential or "").strip()
        if not value:
            raise ValueError("credential is required")
        return hmac.new(self._pepper, value.encode("utf-8"), hashlib.sha256).hexdigest()

    def verify(self, credential: str, expected_digest: str) -> bool:
        candidate = str(credential or "").strip()
        expected = str(expected_digest or "").strip()
        return bool(candidate and len(expected) == 64 and hmac.compare_digest(self.digest(candidate), expected))


def issue_client_secret() -> str:
    return CLIENT_SECRET_PREFIX + _random_text(48)


def hash_client_secret(secret: str, *, salt: bytes | None = None) -> str:
    value = str(secret or "")
    if not value.startswith(CLIENT_SECRET_PREFIX) or len(value.encode("utf-8")) < 48:
        raise ValueError("client secret is invalid")
    actual_salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(value.encode("utf-8"), salt=actual_salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.urlsafe_b64encode(actual_salt).decode("ascii") + "$" + base64.urlsafe_b64encode(digest).decode("ascii")


def verify_client_secret(secret: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt_text, digest_text = str(encoded or "").split("$", 5)
        if algorithm != "scrypt":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
        candidate = hashlib.scrypt(
            str(secret or "").encode("utf-8"),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def _random_text(size: int) -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(size)).rstrip(b"=").decode("ascii")
