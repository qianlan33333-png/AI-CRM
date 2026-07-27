from __future__ import annotations

import pytest

from aicrm_next.platform.navigation_target import resolver


class _FakeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self, size: int = -1) -> bytes:
        return self._body


def test_dynamic_url_link_resolver_allows_wxlink_and_default_short_url_hosts(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        return _FakeResponse(b'{"ok": true, "url_link": "https://wxmpurl.cn/freshLink"}')

    monkeypatch.setattr(resolver, "urlopen", fake_urlopen)

    url = resolver.resolve_dynamic_url_link("https://ip.lhbl.com.cn/api/wxlink?from=qianlan_pay")

    assert url == "https://wxmpurl.cn/freshLink"
    assert captured["url"] == "https://ip.lhbl.com.cn/api/wxlink?from=qianlan_pay"


def test_dynamic_url_link_resolver_blocks_untrusted_source_host() -> None:
    with pytest.raises(Exception, match="host is not allowed"):
        resolver.resolve_dynamic_url_link("https://evil.example/api/wxlink?from=qianlan_pay")


def test_dynamic_url_link_redirect_route_returns_302(next_client, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, "resolve_dynamic_url_link", lambda source_url, response_url_key="url_link": "https://wxaurl.cn/routeLink")

    response = next_client.get(
        "/api/h5/navigation-target/url-link/resolve",
        params={"source_url": "https://ip.lhbl.com.cn/api/wxlink?from=qianlan_pay"},
        follow_redirects=False,
    )

    assert response.status_code == 302
    assert response.headers["location"] == "https://wxaurl.cn/routeLink"
    assert response.headers["X-AICRM-Url-Link-Resolved"] == "true"
