# custats-linux — Design Tokens v2 (Phase 8b)

Phase 8b lifts the menu bar UI toward the visual quality of CodexBar,
token-monitor, TokenTracker, codeburn, ClaudeBar, and Claude-Code-Usage-Monitor.
v2 builds on `docs/design-tokens.md` v1; v1 facts that hold are referenced, not
restated. Net new in v2: refined palette with subtle fills, all 10 provider
glyphs with 2-letter disambiguation, card layout with provider accents, status
pill, sparkline row, themed surface pairs, and a test-mock contract.

## §1. Status colours (refined palette)

Single semantic palette, AA-contrast on both dark (`#1e1e1e`–`#2a2a2a`) and
light (`#fafafa`–`#ffffff`) surfaces. Foregrounds unchanged from v1; subtle
fills are new and used only on pills and overlays, never on bar fill.

| Status     | Foreground | Subtle fill (≈20% α) | Weight  | Label  |
|------------|------------|----------------------|---------|--------|
| GOOD       | `#7AE582`  | `#7AE58233`          | regular | OK     |
| CAUTION    | `#F5C36A`  | `#F5C36A33`          | regular | Watch  |
| CRITICAL   | `#FF7A6B`  | `#FF7A6B33`          | bold    | Warn   |
| AT_LIMIT   | `#FF4D4D`  | `#FF4D4D33`          | bold    | Limit  |
| UNKNOWN    | `#8A8F98`  | `#8A8F9822`          | regular | —      |

Thresholds unchanged (GOOD <70, CAUTION 70–89, CRITICAL 90–99, AT_LIMIT ≥100).
Colour is never the only signal — percent number and one-word label always
appear together. Status colours stay the same in light theme; only the surface
tokens below (§8) flip.

## §2. Provider glyph system

Single-letter default for the 16px tray (terse, font-fallback-safe). Two-letter
variants only where the first letter collides; used in popup rows where 16px is
not a constraint and identity must be unambiguous at a glance.

| Provider    | Tray (1) | Popup (2) | Fallback symbol |
|-------------|----------|-----------|-----------------|
| Claude      | `C`      | `CL`      | ⚡              |
| ChatGPT     | `C`      | `CH`      | —               |
| Codex       | `X`      | `X `      | —               |
| Cursor      | `U`      | `U `      | —               |
| Gemini      | `G`      | `GM`      | ✦               |
| Grok        | `G`      | `GR`      | —               |
| OpenRouter  | `O`      | `OR`      | —               |
| DeepSeek    | `D`      | `D `      | —               |
| Mistral     | `M`      | `M `      | —               |
| Kimi        | `K`      | `K `      | —               |

Two-letter renders in 11pt semibold inside a 22px-wide left cell so column
edges line up across rows.

## §3. Tray icon rules

Codified from v1, extended to all 10 providers:

- **Idle glyph**: `…` (U+2026) in `UNKNOWN` colour when zero accounts OR every
  account's worst status is GOOD/UNKNOWN. Says "nothing to worry about"
  without hiding the icon.
- **Severity overlay**: colour IS the overlay. Never prepend `⚠` or append `!`
  — colour and weight carry the signal.
- **Glyph selection**: when multiple accounts, pick the worst-status account's
  provider letter. Tie-break: provider enum order, then alias (deterministic,
  no flicker across races).
- **Size**: 16 logical pixels on X11/Wayland; `GTK_SCALE` handles HiDPI.
- **Weight**: regular for GOOD/CAUTION/UNKNOWN; bold for CRITICAL/AT_LIMIT.
- **Accessible description**: `"custats — {N} {worst_status}, {M} total"` so
  screen readers announce severity count on focus.

## §4. Popup menu layout (refined)

The flat `GtkMenuItem` list in `popup.py:90-97` becomes a vertical stack of
`Gtk.Box`-based cards. Width 360px, padding 8px outer, 4px gap between cards.
Each card is wrapped in `Gtk.EventBox` so it receives a row-level activate
(opens dashboard focused on that account).

**Per-account card** (top → bottom):
```
┌──────────────────────────────────────┐
││ CL  my-claude · Claude Pro  Watch 78%│   ← header + pill (3px accent at left)
││ ─────────────────────────────────── │
││ 5h  ████████████░░░░ 42%  in 2h 14m │
││ 7d  ██████░░░░░░░░░░ 28%  in 3d 5h  │
││ ─────────────────────────────────── │
││ pace  ▄▆▆▇▆▄▃▂▃  67% Risky            │   ← only when pace_enabled
└──────────────────────────────────────┘
   ↑ 3px provider-accent border at left edge
```

- Header: 11pt semibold alias, 10pt regular `· {provider}` (dim), pill on right.
- Bar: `Gtk.ProgressBar` or `Gtk.LevelBar`, ~220px wide, fraction = pct/100.
  Filled portion uses §1 foreground; unfilled uses §8 `--bar-empty`.
- Bar suffix: `in {reset}` (right of bar, 10pt, dim). Reset uses §5.
- Sparkline row: §7, gated by `pace_enabled` config flag.
- Card border: 1px `--surface-border`, 6px corner radius via CSS provider.

If a window (`5h`/`7d`) is `None` (e.g. Cursor has no 5h), the bar line is
omitted entirely — not greyed out. On `error`, header stays, bar lines replaced
by `! {error}` in `CRITICAL` foreground. Section separators between cards are
dropped — the 3px accent + visible card border makes the card the unit.

## §5. Reset countdown formatting

Unchanged from v1 — restated for the fixer:
- `>1 day`: `"Xd Yh"`
- `>1 hour`: `"Xh Ym"`
- `>1 minute`: `"Xm"`
- `≤ 30s`: `"now"`
- `None`: `""` (line omitted)

Always prefixed by literal `in ` (`in 2h 14m`) so screen readers don't read a
bare duration as a number.

## §6. Status pill

A `Gtk.Box` (visually a label) with §1 subtle fill as background, 6px
horizontal padding, 3px vertical, fully-rounded ends (`border-radius: 9px` via
CSS provider). Content: `{label}` (`OK`/`Watch`/`Warn`/`Limit`/`—`), 11pt
monospace, foreground = §1 foreground, weight = §1 weight.

Pill sits at the right edge of the header line, vertically centred, fixed 48px
wide so all pills line up across rows. Classed `pill-{status}` (e.g.
`pill-watch`) so CSS binds the fill.

## §7. Sparkline (usage history)

30-point mini-chart of the last 30 polls. Two render modes (fixer picks):

1. **Unicode blocks** (default): `▁▂▃▄▅▆▇█` mapped to 8 buckets. Renders as
   a single text string in 10pt mono, survives font fallback, no Cairo.
   Pros: trivial, mock-test friendly. Cons: less smooth.
2. **Cairo line** (optional, opt-in via config): 16×4 px `Gtk.DrawingArea`
   drawing the actual line. Pros: smooth like CodexBar. Cons: needs a
   `Gtk.DrawingArea` mock + Cairo context mock.

Default is mode 1. Fixer's choice signalled by a single constructor argument
`sparkline_mode: str = "unicode"`. Sparkline prefixed by the word `pace` (or
`trend` if no pace projection) so screen readers announce it as data, not
gibberish.

## §8. Theme support

Three themes — surface tokens flip, status colours stay:

| Token               | dark   | light  |
|---------------------|--------|--------|
| `--surface-bg`      | `#1e1e1e` | `#fafafa` |
| `--surface-card`    | `#262626` | `#ffffff` |
| `--surface-border`  | `#2f2f2f` | `#e0e0e0` |
| `--surface-divider` | `#3a3a3a` | `#d8d8d8` |
| `--text-primary`    | `#fafafa` | `#1e1e1e` |
| `--text-dim`        | `#8A8F98` | `#6b6f76` |
| `--bar-empty`       | `#3a3a3a` | `#d8d8d8` |

- `auto`: follow `Gtk.Settings.get_default().gtk_application_prefer_dark_theme`;
  manual override wins.
- `dark` / `light`: forced surface; status colours unchanged.
- Apply via a single `Gtk.CssProvider` loaded at startup; widgets reference
  CSS classes (`card`, `pill`, `pill-{status}`, `bar`) rather than hard-coded
  hex. This is what makes theme-swap a one-line change.

## §9. Per-provider accent (subtle)

3px-wide coloured left border on each card + the same colour at 30% alpha
behind the row's 2-letter glyph cell. Identifies provider at a glance without
competing with status colours.

| Provider    | Accent hex  | Notes                            |
|-------------|-------------|----------------------------------|
| Claude      | `#D97757`   | Anthropic warm                   |
| ChatGPT     | `#10A37F`   | OpenAI green                     |
| Codex       | `#10A37F`   | OpenAI (shared with ChatGPT)     |
| Cursor      | `#000000`   | dark; `#E0E0E0` in light theme   |
| Gemini      | `#4285F4`   | Google blue                      |
| Grok        | `#1E1E1E`   | xAI slate; `#666` in light       |
| OpenRouter  | `#6366F1`   | indigo                           |
| DeepSeek    | `#4D6BFE`   | cobalt                           |
| Mistral     | `#FF7000`   | orange                           |
| Kimi        | `#1E1E1E`   | Moonshot dark; `#666` in light   |

Accent is structural (border + glyph cell), status is informational (fill +
foreground). They never conflict: a healthy Claude row wears Anthropic warm +
green bar; an at-limit Claude row wears Anthropic warm + red bar.

## §10. Empty state

When `len(accounts) == 0`, popup shows a single centred card:
```
┌─────────────────────────────────┐
│   No accounts configured         │   ← 11pt dim text
│   ─────────────────────────────  │
│   Open Dashboard                 │   ← menu item, launches main window
│   Quit                           │   ← menu item
└─────────────────────────────────┘
```

The two menu items sit directly below the empty card. The dim text + divider
IS the visual separator — no extra `Gtk.SeparatorMenuItem` needed.

## §11. Footer

Three menu items below the last card, preceded by a 1px `--surface-divider`:
- **Open Dashboard** — launches main window
- **Refresh now** — forces immediate poll
- **Quit** — exits daemon

Optional fourth (deferred if too much work): **Open data folder** — runs
`xdg-open ~/.local/share/custats` to reveal `state.db` in the file manager.

## §12. Accessibility

- Every card sets `set_tooltip_text(f"{alias} · {provider} · 5h {pct}% · 7d {pct}% · resets {reset}")`
  so hovering reads the full state.
- Status pill uses `Gtk.Accessible.set_role(ROLE_STATUS)` and
  `accessible-label = "Watch at 78%"` — screen readers announce status before
  the bar's percent so colour is never the first signal heard.
- Tab order: card 1 → card 2 → … → footer items. Enter on a card activates it
  (open dashboard focused on that account).
- `Gtk.ProgressBar` always sets `accessible-label = "{pct}% used"` — never
  just the visual fraction.
- Focus rings are not disabled; respect `gtk-application-prefer-dark-theme`
  and high-contrast settings.

## §13. Test-mock contract

GTK is mocked in `tests/test_main_window.py`; v2 stays mock-friendly:

- **Manual child tracking**: `self._live_children: list = []` pattern (already
  in `main_window.py:58, 121`). Every container keeps an explicit list of
  children it added; updates clear, remove, re-add. Never rely on
  `get_children()` from a mock.
- **Widget identity by handle**: thin wrappers (`Card`, `Bar`, `Pill`) hold a
  `widget` attr and forward `.set_*` calls. Tests assert against wrappers.
- **Pure-data builders**: `_build_account_card(Gtk, status, theme, sparkline_mode)`
  is pure — same input → same widget tree, golden-test friendly.
- **No CSS provider at import time**: loads in `_apply_theme` once at startup.
- **Lazy GTK**: `_try_gtk()` stays at top of every UI file (existing pattern).

## §14. Implementation handoff notes for the @fixer

- `_STATUS_COLOURS_HEX` in `tray.py:45-51` is the source of truth; popup
  imports it.
- `_PROVIDER_GLYPH` in `tray.py:54` covers 4; v2 needs all 10. Fixer extends;
  `tray.py` stays canonical.
- `humanize_reset` in `popup.py:8-29` is unchanged.
- Phase 3 poller writes history to SQLite; add
  `db.recent_usage_points(account_id, n=30) -> list[float]` if missing.
- 2-letter glyph in popup only; tray keeps single letters per §3.
- Card: `Gtk.Frame` with CSS
  `border-left: 3px solid {accent}; border-radius: 6px; background: {surface-card}`.
- **Deferred**: Cairo sparkline (Unicode only), "Open data folder" footer
  (punted), Enter-on-card (fixer decides), transitions (none in v2).
