"""Registry of providers with browser OAuth support.

Today this contains Codex, ChatGPT (both reuse OpenAI's
``auth.openai.com`` device-code endpoint with a different
``device_code_hint``), and GitHub Copilot (GitHub's public
``github.com/login/device/code`` endpoint). Adding Claude/Grok/Cursor
requires their providers to publish OAuth device-code endpoints — not
currently the case. The :data:`SUPPORTED` dict lets the CLI list
what's available and the test suite assert coverage of the registered
providers.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Optional

from ..core.models import Provider
from .device_code import DeviceCodeApproved, DeviceCodeRequest

# A browser sign-in flow is ``async (client, on_poll) -> (initial, final)``.
# We type it loosely here (``Any`` for the client/callback) to avoid an
# import cycle with :mod:`custats.oauth.codex` — the type checker is
# happy because we re-bind ``begin`` at the bottom of the module.
BrowserFlow = Callable[..., Awaitable[tuple[DeviceCodeRequest, DeviceCodeApproved]]]

# Map Provider -> BrowserFlow. Initially empty; populated below once
# the per-provider module is imported (avoids the circular import
# between this module and :mod:`custats.oauth.codex`).
SUPPORTED: dict[Provider, Optional[BrowserFlow]] = {}


# Populate the registry after the type alias exists. Using a deferred
# import keeps the module-level ``SUPPORTED`` value consistent with
# the imports above and avoids the cycle at import time.
from . import chatgpt as _chatgpt  # noqa: E402
from . import codex as _codex  # noqa: E402
from . import copilot as _copilot  # noqa: E402

SUPPORTED[Provider.CODEX] = _codex.begin
SUPPORTED[Provider.CHATGPT] = _chatgpt.begin
SUPPORTED[Provider.COPILOT] = _copilot.begin

# Silence the unused-argument warning for ``Any`` in the type alias —
# ``Any`` is a valid placeholder for the client argument and we want
# to keep the alias readable.
_ = Any


__all__ = ["BrowserFlow", "SUPPORTED"]
