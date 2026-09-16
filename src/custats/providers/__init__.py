"""Provider adapters for custats.

Each adapter is a small async fetcher that takes a credentials dict
and an :class:`httpx.AsyncClient`, calls the provider's usage
endpoint, and returns a :class:`custats.core.models.Usage` snapshot.

Use :func:`get_adapter` to look one up by :class:`Provider` enum,
or :data:`ADAPTERS` to iterate over every supported provider.
"""

from __future__ import annotations

from ..core.models import Provider
from .base import (
    AdapterError,
    AuthError,
    ProviderAdapter,
    ProviderUnavailable,
    RateLimitError,
)
from .chatgpt import ChatGPTAdapter
from .claude import ClaudeAdapter
from .codex import CodexAdapter
from .copilot import GitHubCopilotAdapter
from .cursor import CursorAdapter
from .deepseek import DeepSeekAdapter
from .gemini import GeminiAdapter
from .grok import GrokAdapter
from .kimi import KimiAdapter
from .mistral import MistralAdapter
from .openrouter import OpenRouterAdapter

ADAPTERS: dict[Provider, ProviderAdapter] = {
    Provider.CLAUDE: ClaudeAdapter(),
    Provider.CHATGPT: ChatGPTAdapter(),
    Provider.CODEX: CodexAdapter(),
    Provider.COPILOT: GitHubCopilotAdapter(),
    Provider.GEMINI: GeminiAdapter(),
    Provider.GROK: GrokAdapter(),
    Provider.OPENROUTER: OpenRouterAdapter(),
    Provider.DEEPSEEK: DeepSeekAdapter(),
    Provider.CURSOR: CursorAdapter(),
    Provider.MISTRAL: MistralAdapter(),
    Provider.KIMI: KimiAdapter(),
}


def get_adapter(provider: Provider) -> ProviderAdapter:
    """Return the adapter registered for ``provider``.

    Raises :class:`KeyError` (i.e. it's a programmer error) if no
    adapter is registered — every enum member should map to one.
    """
    return ADAPTERS[provider]


__all__ = [
    "ADAPTERS",
    "AdapterError",
    "AuthError",
    "ChatGPTAdapter",
    "ClaudeAdapter",
    "CodexAdapter",
    "CursorAdapter",
    "DeepSeekAdapter",
    "GeminiAdapter",
    "GitHubCopilotAdapter",
    "GrokAdapter",
    "KimiAdapter",
    "MistralAdapter",
    "OpenRouterAdapter",
    "ProviderAdapter",
    "ProviderUnavailable",
    "RateLimitError",
    "get_adapter",
]
