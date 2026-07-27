from __future__ import annotations

from typing import Any, Protocol

from aicrm_next.shared.repository_provider import RepositoryProviderError


class ChannelCompletionReadPort(Protocol):
    def get_channel_qr(self, channel_id: int) -> dict[str, Any] | None: ...

    def require_usable_channel_qr(self, channel_id: int) -> dict[str, Any]: ...


_provider: ChannelCompletionReadPort | None = None


def configure_channel_completion_provider(provider: ChannelCompletionReadPort) -> None:
    global _provider
    _provider = provider


class ChannelCompletionClient:
    """Questionnaire-facing port composed with the channel-owned read service."""

    @staticmethod
    def _configured_provider() -> ChannelCompletionReadPort:
        if _provider is None:
            raise RepositoryProviderError("channel completion provider is unavailable")
        return _provider

    def get_channel_qr(self, channel_id: int) -> dict[str, Any] | None:
        return self._configured_provider().get_channel_qr(channel_id)

    def require_usable_channel_qr(self, channel_id: int) -> dict[str, Any]:
        return self._configured_provider().require_usable_channel_qr(channel_id)


__all__ = [
    "ChannelCompletionClient",
    "ChannelCompletionReadPort",
    "configure_channel_completion_provider",
]
