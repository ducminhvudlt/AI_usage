"""Browser OAuth sign-in flows for custats.

Phase 7a adds a generic OAuth 2.0 device-code sign-in flow (RFC 8628)
used by Codex today. The package is structured so additional providers
can plug in a thin :func:`begin` adapter without changing the CLI or
the polling logic.
"""

from __future__ import annotations

__all__: list[str] = []
