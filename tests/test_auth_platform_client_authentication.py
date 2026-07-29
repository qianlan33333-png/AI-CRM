import base64

import pytest

from aicrm_next.platform.platform_foundation.auth_platform.client_authentication import (
    ClientAuthenticationError,
    client_credentials,
    request_source_ip,
)


def test_client_credentials_accepts_basic_or_form_and_rejects_conflicts() -> None:
    basic = base64.b64encode(b"client-a:secret-a").decode("ascii")
    assert client_credentials(headers={"Authorization": f"Basic {basic}"}, form={}) == ("client-a", "secret-a")
    assert client_credentials(headers={}, form={"client_id": "client-b", "client_secret": "secret-b"}) == (
        "client-b",
        "secret-b",
    )

    with pytest.raises(ClientAuthenticationError, match="invalid_client"):
        client_credentials(
            headers={"Authorization": f"Basic {basic}"},
            form={"client_id": "different", "client_secret": "secret-a"},
        )
    with pytest.raises(ClientAuthenticationError, match="invalid_client"):
        client_credentials(headers={"Authorization": "Bearer shared-token"}, form={})


def test_request_source_ip_trusts_forwarding_only_from_registered_proxy() -> None:
    headers = {"X-Forwarded-For": "203.0.113.8, 10.0.0.2"}
    assert request_source_ip(peer_ip="10.0.0.2", headers=headers, trusted_proxy_cidrs=("10.0.0.0/8",)) == "203.0.113.8"
    assert request_source_ip(peer_ip="198.51.100.9", headers=headers, trusted_proxy_cidrs=("10.0.0.0/8",)) == "198.51.100.9"

    with pytest.raises(ClientAuthenticationError, match="invalid_forwarded_client_ip"):
        request_source_ip(
            peer_ip="10.0.0.2",
            headers={"X-Forwarded-For": "not-an-ip"},
            trusted_proxy_cidrs=("10.0.0.0/8",),
        )
