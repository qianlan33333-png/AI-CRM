from aicrm_next.platform.platform_foundation.auth_platform.credentials import (
    CSRF_PREFIX,
    SESSION_PREFIX,
    CredentialHasher,
    hash_client_secret,
    issue_client_secret,
    verify_client_secret,
)


def test_server_credentials_are_high_entropy_and_only_digests_are_stored() -> None:
    hasher = CredentialHasher("p" * 32)
    session = hasher.issue(SESSION_PREFIX)
    csrf = hasher.issue(CSRF_PREFIX)

    assert session.value.startswith("ss_")
    assert csrf.value.startswith("csrf_")
    assert len(session.digest) == 64
    assert session.value not in repr(session.digest)
    assert hasher.verify(session.value, session.digest)
    assert not hasher.verify(csrf.value, session.digest)


def test_client_secret_uses_scrypt_and_verifies_without_plaintext_storage() -> None:
    secret = issue_client_secret()
    encoded = hash_client_secret(secret, salt=b"0123456789abcdef")

    assert secret.startswith("aics_")
    assert encoded.startswith("scrypt$16384$8$1$")
    assert secret not in encoded
    assert verify_client_secret(secret, encoded)
    assert not verify_client_secret(issue_client_secret(), encoded)
