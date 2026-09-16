"""Two-tier provider glyph map, accent colours, and status tokens.

Single letters for the 16px tray icon (terse).
Two-letter disambiguation for the popup rows where letter-collisions matter.

The values are sourced from ``docs/design-tokens-v2.md`` §2 and §9, plus
the v3 additions for status shapes (§1), status badges (§2), settings
legend (§6), and light-theme accent darkening (§7). Kept in a tiny
module so ``tray.py`` and ``popup.py`` can share one source of truth
without dragging GTK into a pure-data file.
"""
from __future__ import annotations

from custats.core.models import Provider

# Single letter — used by tray icon (16px).
PROVIDER_GLYPH = {
    Provider.CLAUDE: "C",
    Provider.CHATGPT: "C",       # collision resolved by 2-letter in popup
    Provider.CODEX: "X",
    Provider.COPILOT: "P",       # GitHub-style initial for the new provider.
    Provider.CURSOR: "U",
    Provider.GEMINI: "G",
    Provider.GROK: "G",
    Provider.OPENROUTER: "O",
    Provider.DEEPSEEK: "D",
    Provider.MISTRAL: "M",
    Provider.KIMI: "K",
}

# Two-letter — used by popup rows where identity must be unambiguous at a glance.
PROVIDER_GLYPH_2 = {
    Provider.CLAUDE: "CL",
    Provider.CHATGPT: "CH",
    Provider.CODEX: "CX",
    Provider.COPILOT: "CP",
    Provider.CURSOR: "CU",
    Provider.GEMINI: "GM",
    Provider.GROK: "GR",
    Provider.OPENROUTER: "OR",
    Provider.DEEPSEEK: "DS",
    Provider.MISTRAL: "MS",
    Provider.KIMI: "KM",
}

# Per-provider accent (3px left border on rows / frames; glyph cell at 30% alpha).
# Source-of-truth dark-theme values; light-theme variants live below.
PROVIDER_ACCENT_HEX = {
    Provider.CLAUDE: "#D97757",
    Provider.CHATGPT: "#10A37F",
    Provider.CODEX: "#10A37F",
    Provider.COPILOT: "#181717",   # GitHub brand slate
    Provider.CURSOR: "#000000",
    Provider.GEMINI: "#4285F4",
    Provider.GROK: "#1E1E1E",
    Provider.OPENROUTER: "#6366F1",
    Provider.DEEPSEEK: "#4D6BFE",
    Provider.MISTRAL: "#FF7000",
    Provider.KIMI: "#1E1E1E",
}

# v3 §7 — light-theme accent darkening. Each source-of-truth value is multiplied
# by 0.85 at widget-construction time (not in this file) so the hex table
# remains importable without theme branching. The table is pre-computed per the
# design doc so tests / callers see stable values rather than re-deriving them
# from floats.
PROVIDER_ACCENT_HEX_LIGHT = {
    Provider.CLAUDE:     "#B46347",
    Provider.CHATGPT:    "#0E8C6C",
    Provider.CODEX:      "#0E8C6C",
    Provider.COPILOT:    "#131313",
    Provider.CURSOR:     "#000000",
    Provider.GEMINI:     "#3973CD",
    Provider.GROK:    "#191919",
    Provider.OPENROUTER: "#5458CE",
    Provider.DEEPSEEK:   "#415BD5",
    Provider.MISTRAL:    "#D96000",
    Provider.KIMI:    "#191919",
}

# v3 §1 — Tray shape per status. Pango text, no Cairo asset required.
# GOOD and UNKNOWN share the outline circle (the only visual difference is
# colour: green for GOOD, muted for UNKNOWN / idle).
_STATUS_SHAPE = {
    "GOOD":     "○",   # U+25CB  — circle outline
    "CAUTION":  "◐",   # U+25D0  — circle with lower half filled
    "CRITICAL": "△",   # U+25B3  — triangle outline
    "AT_LIMIT": "■",   # U+25A0  — filled square
    "UNKNOWN":  "○",   # idle / no-data, same outline as GOOD but muted colour
}

# v3 §2 — Status badge dot + one-word label for popup rows.
# The dot character itself is the glyph; colour comes from
# ``STATUS_COLOURS_HEX_POPUP`` keyed by the status below.
_STATUS_DOT = {
    "GOOD":     ("●", "OK"),
    "CAUTION":  ("●", "Watch"),
    "CRITICAL": ("●", "Warn"),
    "AT_LIMIT": ("●", "Limit"),
    "UNKNOWN":  ("○", "—"),
}

# Alias kept for back-compat with code that imported the v2 dict name.
_STATUS_PILL_LABEL = {status: dot[1] for status, dot in _STATUS_DOT.items()}

# v3 §2 — popup status foregrounds (carry over from v2 unchanged; the tray
# uses a darker variant dict that lives in ``tray.py``).
STATUS_COLOURS_HEX_POPUP = {
    "GOOD":     "#7AE582",
    "CAUTION":  "#F5C36A",
    "CRITICAL": "#FF7A6B",
    "AT_LIMIT": "#FF4D4D",
    "UNKNOWN":  "#8A8F98",
}

# v3 §6 — two-line legend rendered at the bottom of the Settings tab.
_STATUS_LEGEND_TEXT = "Status legend: ● OK   ● Watch   ● Warn   ● Limit"
_SHAPE_LEGEND_TEXT = "Shape key:     ○ outline   ◐ half-filled   △ triangle   ■ filled square"


def accent_hex_for(provider: Provider, theme: str) -> str:
    """Return the accent hex for ``provider`` adjusted for ``theme``.

    For ``theme == "light"`` returns the pre-computed darkened variant
    from :data:`PROVIDER_ACCENT_HEX_LIGHT`; otherwise the source-of-truth
    dark value. Falls back to a neutral grey for unknown providers so the
    widget construction never raises.
    """
    table = PROVIDER_ACCENT_HEX_LIGHT if (theme or "").lower() == "light" else PROVIDER_ACCENT_HEX
    return table.get(provider, "#888888")


__all__ = [
    "PROVIDER_GLYPH",
    "PROVIDER_GLYPH_2",
    "PROVIDER_ACCENT_HEX",
    "PROVIDER_ACCENT_HEX_LIGHT",
    "accent_hex_for",
    "STATUS_COLOURS_HEX_POPUP",
    "_STATUS_SHAPE",
    "_STATUS_DOT",
    "_STATUS_PILL_LABEL",
    "_STATUS_LEGEND_TEXT",
    "_SHAPE_LEGEND_TEXT",
]