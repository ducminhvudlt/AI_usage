"""Domain models for custats.

The models in this module are pure dataclasses / enums from the
standard library. They have no I/O, no providers, and no GUI
concerns so they can be reused across every layer of the project.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal


class Provider(str, Enum):
    """AI providers that custats can monitor."""

    CLAUDE = "claude"
    CHATGPT = "chatgpt"
    CODEX = "codex"
    COPILOT = "copilot"
    GEMINI = "gemini"
    GROK = "grok"
    OPENROUTER = "openrouter"
    DEEPSEEK = "deepseek"
    MISTRAL = "mistral"
    KIMI = "kimi"
    CURSOR = "cursor"

    @classmethod
    def parse(cls, value: str) -> "Provider":
        """Case-insensitive parser accepting both enum name and value.

        Raises:
            ValueError: if ``value`` does not match any provider.
        """
        if not isinstance(value, str):
            raise ValueError(f"provider must be a string, got {type(value).__name__}")
        normalized = value.strip().lower()
        for member in cls:
            if member.name.lower() == normalized or member.value == normalized:
                return member
        raise ValueError(f"unknown provider: {value!r}")


class UsageStatus(str, Enum):
    """Severity ordering for usage buckets.

    Ordered from least to most severe so that ``max()`` over a set
    of statuses yields the worst one. ``UNKNOWN`` is intentionally
    the lowest — when data is missing we don't want to claim
    everything is fine.
    """

    UNKNOWN = "unknown"
    GOOD = "good"
    CAUTION = "caution"
    CRITICAL = "critical"
    AT_LIMIT = "at_limit"

    @property
    def severity(self) -> int:
        """Numeric severity used for comparisons (higher = worse)."""
        order = {
            UsageStatus.UNKNOWN: 0,
            UsageStatus.GOOD: 1,
            UsageStatus.CAUTION: 2,
            UsageStatus.CRITICAL: 3,
            UsageStatus.AT_LIMIT: 4,
        }
        return order[self]


@dataclass(frozen=True)
class ProviderLimits:
    """Raw percentage usage for the provider's two reset windows.

    Both fields are optional because not every provider exposes both
    windows and some upstream responses may omit fields. Provider
    specific extras (``grok_window``, ``codex_reset_credits``) are
    kept as optional typed fields here so future code can read them
    without churning this dataclass.
    """

    five_hour_percent: float | None = None
    seven_day_percent: float | None = None
    grok_window: Literal["weekly", "monthly"] | None = None
    codex_reset_credits: int | None = None
    cursor_plan: Literal["free", "pro", "business", "enterprise"] | None = None
    monthly_percent: float | None = None


@dataclass
class Usage:
    """A snapshot of a single account's usage limits."""

    account_id: str
    provider: Provider
    fetched_at: datetime
    # Both windows are None-able: Cursor has no 5-hour window, Claude
    # Pro may have only a five-hour window, etc. Adapters must populate
    # whichever window(s) the upstream API actually exposes.
    five_hour: ProviderLimits | None = None
    five_hour_resets_at: datetime | None = None
    seven_day: ProviderLimits | None = None
    seven_day_resets_at: datetime | None = None
    extra_credits_remaining: float | None = None
    raw: dict[str, Any] | None = None


@dataclass
class Account:
    """A tracked provider account."""

    id: str
    alias: str
    provider: Provider
    created_at: datetime
    last_seen_at: datetime | None = None
    is_active: bool = True

    @staticmethod
    def new_id() -> str:
        """Return a fresh hex UUID4 string suitable for the primary key."""
        return uuid.uuid4().hex


__all__ = [
    "Account",
    "Provider",
    "ProviderLimits",
    "Usage",
    "UsageStatus",
]
