"""Application configuration.

Honors the XDG Base Directory specification: configuration lives in
``$XDG_CONFIG_HOME/custats`` (default ``~/.config/custats``) and
mutable state (SQLite DB) lives in ``$XDG_DATA_HOME/custats``
(default ``~/.local/share/custats``).

The on-disk format is a tiny subset of TOML that we read with
``tomllib`` and write with a small serializer in this module —
that keeps the dependency footprint minimal (no ``tomli_w``).
"""

from __future__ import annotations

import os
import sys
import tomllib
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Mapping

from .models import Provider

CONFIG_FILENAME = "config.toml"
DB_FILENAME = "state.db"
KEY_FILENAME = "secret.key"


def _xdg_path(env_var: str, default: str) -> Path:
    """Resolve an XDG directory from env or default."""
    raw = os.environ.get(env_var)
    if raw:
        base = Path(raw)
    else:
        base = Path(default)
    if base.is_absolute() or raw:
        return base
    return Path.home() / default.lstrip("~/").lstrip("/")


def config_dir() -> Path:
    """Return ``$XDG_CONFIG_HOME/custats`` (creating it if missing)."""
    path = _xdg_path("XDG_CONFIG_HOME", "~/.config") / "custats"
    path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir() -> Path:
    """Return ``$XDG_DATA_HOME/custats`` (creating it if missing)."""
    path = _xdg_path("XDG_DATA_HOME", "~/.local/share") / "custats"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_file() -> Path:
    """Full path to the TOML config file."""
    return config_dir() / CONFIG_FILENAME


def db_file() -> Path:
    """Full path to the SQLite database file."""
    return state_dir() / DB_FILENAME


def secret_key_file() -> Path:
    """Full path to the AES-GCM key blob."""
    return config_dir() / KEY_FILENAME


@dataclass
class AppConfig:
    """User preferences persisted to ``config.toml``."""

    refresh_interval_seconds: int = 60
    notify_threshold_percent: int = 80
    notify_on_recovery: bool = True
    show_in_menu_bar: dict[Provider, bool] = field(
        default_factory=lambda: {p: True for p in Provider}
    )
    pace_enabled: bool = True
    theme: str = "auto"  # one of "auto", "light", "dark"
    # Per-account notification opt-out (design-tokens-v3 §10, formerly
    # deferred). Keys are ``Account.id`` strings; a missing key means the
    # account notifies (default-on). Only touched by
    # :meth:`is_notify_enabled` / :meth:`set_notify_enabled`.
    notify_accounts: dict[str, bool] = field(default_factory=dict)

    # ------------------------------------------------------------------ #
    # helpers
    # ------------------------------------------------------------------ #

    def is_provider_visible(self, provider: Provider) -> bool:
        return self.show_in_menu_bar.get(provider, True)

    def set_provider_visible(self, provider: Provider, visible: bool) -> None:
        self.show_in_menu_bar[provider] = visible

    def is_notify_enabled(self, account_id: str) -> bool:
        """True when ``account_id`` should fire notifications (default-on)."""
        return self.notify_accounts.get(account_id, True)

    def set_notify_enabled(self, account_id: str, enabled: bool) -> None:
        """Record the per-account notification preference."""
        self.notify_accounts[account_id] = bool(enabled)

    # ------------------------------------------------------------------ #
    # (de)serialization
    # ------------------------------------------------------------------ #

    def to_mapping(self) -> dict[str, Any]:
        """Return a primitive ``dict`` ready for serialization."""
        data = asdict(self)
        # ``asdict`` keeps dataclasses intact; convert Provider enums to
        # their string value so the on-disk format stays human-readable.
        data["show_in_menu_bar"] = {
            p.value: visible for p, visible in self.show_in_menu_bar.items()
        }
        # notify_accounts keys are arbitrary account-id strings — coerce
        # defensively so the on-disk format stays TOML-safe.
        data["notify_accounts"] = {
            str(k): bool(v) for k, v in self.notify_accounts.items()
        }
        return data

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "AppConfig":
        """Build an :class:`AppConfig` from a parsed TOML mapping."""
        kwargs: dict[str, Any] = {}
        for f in cls.__dataclass_fields__:  # type: ignore[attr-defined]
            if f in data:
                kwargs[f] = data[f]
        # Convert provider strings back to enum members.
        if "show_in_menu_bar" in kwargs:
            raw = kwargs["show_in_menu_bar"]
            show: dict[Provider, bool] = {}
            if isinstance(raw, Mapping):
                for key, value in raw.items():
                    try:
                        provider = Provider.parse(str(key))
                    except ValueError:
                        continue
                    show[provider] = bool(value)
            for provider in Provider:
                show.setdefault(provider, True)
            kwargs["show_in_menu_bar"] = show
        # Coerce notify_accounts values to bool (str "false" etc. would
        # otherwise stay truthy strings).
        if "notify_accounts" in kwargs and isinstance(
            kwargs["notify_accounts"], Mapping
        ):
            kwargs["notify_accounts"] = {
                str(k): bool(v) for k, v in kwargs["notify_accounts"].items()
            }
        return cls(**kwargs)


# ---------------------------------------------------------------------- #
# TOML (de)serialization
# ---------------------------------------------------------------------- #


def _toml_escape(value: str) -> str:
    """Escape a string for a basic TOML literal.

    Per TOML 1.0, characters in the ranges U+0000..U+0008, U+000A..U+001F,
    and U+007F must be escaped. We use the short forms for the common
    cases (``\\b``, ``\\t``, ``\\n``, ``\\f``, ``\\r``) and ``\\u00XX``
    for everything else in those ranges.
    """
    out: list[str] = []
    for ch in value:
        cp = ord(ch)
        if ch == "\\":
            out.append("\\\\")
        elif ch == '"':
            out.append('\\"')
        elif ch == "\b":
            out.append("\\b")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\f":
            out.append("\\f")
        elif ch == "\r":
            out.append("\\r")
        elif cp <= 0x08 or (cp >= 0x0A and cp <= 0x1F) or cp == 0x7F:
            out.append(f"\\u{cp:04X}")
        else:
            out.append(ch)
    return "".join(out)


def _dump_value(value: Any) -> str:
    """Render a primitive Python value as a TOML fragment."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{_toml_escape(value)}"'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value:  # NaN
            raise ValueError("NaN is not a valid TOML float")
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_dump_value(v) for v in value) + "]"
    if isinstance(value, dict):
        inner = ", ".join(
            f'"{_toml_escape(str(k))}" = {_dump_value(v)}' for k, v in value.items()
        )
        return "{" + inner + "}"
    raise TypeError(f"unsupported TOML value type: {type(value).__name__}")


def _dump_toml(mapping: Mapping[str, Any]) -> str:
    """Serialize a flat-ish mapping as a tiny TOML document.

    Dicts are emitted as inline tables (``{k = v, ...}``) rather than
    ``[section]`` headers — a ``[section]`` header would scoop every
    following top-level key into that section, which is almost never
    what we want for a flat config like ``AppConfig``.
    """
    lines: list[str] = []
    for key, value in mapping.items():
        lines.append(f"{key} = {_dump_value(value)}")
    return "\n".join(lines).rstrip() + "\n"


def load_config(path: Path | None = None) -> AppConfig:
    """Load :class:`AppConfig` from disk, falling back to defaults."""
    target = path or config_file()
    if not target.exists():
        return AppConfig()
    with target.open("rb") as fh:
        data = tomllib.load(fh)
    if not isinstance(data, Mapping):
        return AppConfig()
    return AppConfig.from_mapping(data)


def save_config(cfg: AppConfig, path: Path | None = None) -> None:
    """Persist :class:`AppConfig` to disk (atomic-ish write)."""
    target = path or config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = cfg.to_mapping()
    rendered = _dump_toml(payload)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(rendered, encoding="utf-8")
    tmp.replace(target)


__all__ = [
    "AppConfig",
    "config_dir",
    "config_file",
    "db_file",
    "load_config",
    "save_config",
    "secret_key_file",
    "state_dir",
]
