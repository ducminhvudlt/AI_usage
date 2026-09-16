# custats-linux — Design Tokens v3 (Phase 9b)

v3 layers **visual state variants** over v2 so users can read tray state at a
glance without opening the popup — patterns from CodexBar's coloured icon
bar, ClaudeBar's Touch Bar pixel mascot, TokenTracker's bar+glyph hybrid.
v2 facts that hold (palette, accents, surface tokens, test-mock contract) are
referenced, not restated.

The headline change is a **shape system** for the tray icon: every status
gets a distinct shape *and* colour, so colour-blind users and dim top-bars
read severity from outline alone.

## §1. Tray icon — shape variants per status

Drawn into a 16×16 Pango `CairoSurface` (same path as v2 §3). Centred in the
cell, ~12px tall with 2px padding.

| Status                       | Shape                           | Fill colour              | Trailing glyph |
|------------------------------|---------------------------------|--------------------------|----------------|
| `GOOD` / `UNKNOWN` (all-good)| circle outline `○`              | `#3a9d5d` (good) / `#8A8F98` (muted) | — |
| `CAUTION`                    | circle outline + lower-half `◐` | `#d4a72c` (amber)        | — |
| `CRITICAL`                   | triangle outline `△`            | `#FF7A6B` (coral)        | `!` (white) inside |
| `AT_LIMIT`                   | filled square `■`               | `#FF4D4D` (red)          | `×` (white) inside |

Shape is the redundant signal. The provider letter is dropped when it would
collide with the shape at 16px (e.g. `C` and `■` together are illegible).
Idle / zero-accounts uses the `UNKNOWN` outline `○` with no glyph — replaces
v2's `…`, which reads as noise in 3px horizontal ellipsis.

GOOD/CAUTION hex values are slightly darker than v2 §1 (`#7AE582` / `#F5C36A`)
because the tray needs more chroma separation from sibling tray icons at
16px. Popup row pills keep the v2 foregrounds for AA contrast on card
surfaces. `_STATUS_COLOURS_HEX` in `tray.py:41` becomes tray-only; popup
imports a separate dict.

## §2. Status badge (popup rows)

Replaces v2 §6's filled pill with a **dot + label** in the row header. Sits
at the right edge of the header, vertically centred, fixed 56px wide.

- `OK` → green dot `●` + "OK"
- `Watch` → amber dot `●` + "Watch"
- `Warn` → coral dot `●` + "Warn"
- `Limit` → red dot `●` + "Limit"
- `—` → muted outline dot `○` + "—"

Unicode `●` (U+25CF) and `○` (U+25CB), 11pt monospace, 4px horizontal
padding, foreground per §1. The dot IS the colour signal — pills lose
their background fill, reading lighter on busy desktops and reclaiming
~12px of horizontal space for the alias.

## §3. Inline percent + reset chip

Bar rows lose `Gtk.ProgressBar`. Each row is one monospace text line with
right-aligned percent so digits line up across rows:

```
5h  ▓▓▓▓▓▓▓▓▓░░░░░  42%  ↻ 2h 14m
```

- **Bar**: 14 cells, `█` (U+2588) filled, `░` (U+2591) empty. Width is
  constant per row regardless of percent — digits do the talking.
- **Percent**: right-aligned, 4 chars wide (` 42%`, `100%`), monospace.
- **`↻` glyph** (U+21BB CLOCKWISE OPEN CIRCLE ARROW) before the reset
  countdown. Replaces v2's literal `in ` prefix. Visually marks the value
  as a recurring reset, not a one-shot event.
- **Reset**: existing `humanize_reset` form (`popup.py:18`).

Cards with `pct is None` (e.g. Cursor's 5h) skip the entire line — same as
v2 §4. Error cards keep the `! {error}` line. **Screen-reader note**: `↻`
is read by AT as "clockwise open circle arrow", worse than v2's "in 2h
14m". Fixer should set the row's `accessible-label` to
`"{pct}% used, resets in {reset}"` so AT still hears the prose prefix
while sighted users see the symbol.

## §4. Pace sparkline moves inline

The sparkline moves from a separate row below the bars (v2 §4) onto the
pace line itself:

```
Pace  ▁▂▃▅▆▅▃▂▁  67% Risky
```

Bar + percent + label on one line, monospace, gated by `pace_enabled` (v2).
Sparkline uses **10 cells** (was 8) for finer detail — same `▁▂▃▄▅▆▇█`
buckets. `_sparkline` in `_components.py:29` bucket count becomes 10; the
percent column widens from 3 to 4 chars to match §3.

## §5. Empty state card

Replaces v2 §10's dim text + footer with a friendly 3-step onboarding card,
same frame as account rows:

```
┌─────────────────────────────────────┐
│ Welcome to custats                  │   ← 13pt bold header
│ ─────────────────────────────────── │
│ ① Pick providers — Claude, Codex,   │   ← 11pt body, numbered
│   GitHub, OpenAI, Cursor, ...        │
│ ② Add with one command              │
│ ③ Run `custats run` to start        │
│                                     │
│ [ Run `custats onboard` ]            │   ← accent-coloured "button"
└─────────────────────────────────────┘
```

Numbered glyphs `①` `②` `③` (U+2460–U+2462) survive font fallback. The
`[ Run \`custats onboard\` ]` line is a Pango span in
`PROVIDER_ACCENT_HEX[Provider.CLAUDE]` (Anthropic warm `#D97757`), 11pt
semibold. The card stays clickable via `Gtk.MenuItem` and calls
`on_open_dashboard` — mirrors v2's "Open Settings to add one" behaviour.

## §6. Status legend (Settings tab)

Two-line legend at the bottom of the Settings tab, below the per-provider
visibility switches. 10pt monospace, `--text-dim`:

```
Status legend: ● OK   ● Watch   ● Warn   ● Limit
Shape key:     ○ outline   ◐ half-filled   △ triangle   ■ filled square
```

Same dots as §2. Shape key teaches the tray encoding — always visible,
not theme-gated.

## §7. Theme accent — light-theme darkening

v2 §8 surface tokens unchanged. v3 adds **accent darkening in light theme**:
each `PROVIDER_ACCENT_HEX` value is multiplied by `0.85` when `theme ==
"light"` to keep AA contrast against the white card surface (`#ffffff`).
Applied at widget-construction time, not in `_glyphs.py` — the source-of-
truth hex stays at full chroma so test code can import `PROVIDER_ACCENT_HEX`
without theme branching.

Approximate light-theme values the fixer pre-computes (ChatGPT/Codex
share `#10A37F` → `#0E8C6C`; Cursor/Grok/Kimi barely shift):

| Provider      | dark      | light (×0.85) |
|---------------|-----------|---------------|
| Claude        | `#D97757` | `#B46347`      |
| ChatGPT/Codex | `#10A37F` | `#0E8C6C`      |
| Cursor        | `#000000` | `#000000`      |
| Gemini        | `#4285F4` | `#3973CD`      |
| Grok/Kimi     | `#1E1E1E` | `#191919`      |
| OpenRouter    | `#6366F1` | `#5458CE`      |
| DeepSeek      | `#4D6BFE` | `#415BD5`      |
| Mistral       | `#FF7000` | `#D96000`      |

Fixer caches the table per session rather than recomputing per widget.

## §8. Accessibility — keyboard hint footer

A muted single-line hint sits below the last account card, above the divider:

```
↑↓ navigate · Enter open · R refresh · Q quit
```

10pt `--text-dim`, monospace, full-width inside the popup. **Only renders
when `len(account_rows) >= 3`** — fewer rows means nothing to navigate, and
the hint becomes noise. UNKNOWN-status accounts count toward the threshold.

Informational, not interactive — real keybindings remain GTK defaults.

## §9. Test-mock contract

Same as v2 §13. New v3 reminders:

- MagicMock GTK, no asset files. Pango text only — `○`, `◐`, `△`, `■`
  pass through unchanged. No Cairo paths in the tray.
- `tray.TrayIcon` gains `_last_shape: str | None` alongside
  `_last_glyph_key`; tests assert against both.
- `_AccountCard` gains `badge_glyph: str` and `bar_text_5h: str` /
  `bar_text_7d: str` typed attributes so tests don't crawl widgets.
- Empty-state wrapper holds `welcome_card: bool` so tests assert the
  onboarding path without rendering.
- Manual child tracking continues (`_live_children`, `account_rows`).
  Never rely on `get_children()`.

## §10. Deferred — status update

Two previously deferred items have since **shipped**:

- **"Open data folder" footer item** — popup footer entry that opens
  ``state_dir()`` (``~/.local/share/custats``) via ``xdg-open``. Rendered
  only when the caller passes ``on_open_data_folder`` to
  :class:`~custats.ui.popup.PopupMenu` /
  :class:`~custats.ui.tray.TrayIcon`; the tray always does.
- **Status-change alert animation** — a worsening severity transition
  (e.g. CAUTION → CRITICAL) flips the indicator to
  ``AppIndicator3.IndicatorStatus.ATTENTION`` for
  ``_ALERT_BLINK_SECONDS`` (4 s), which attention-capable panels render
  as a highlight/pulse, then reverts to ``ACTIVE``. Improvements never
  alert — recovery is the Notifier's job. Test contract:
  ``TrayIcon._attention_on`` plus ``set_status`` call assertions.

Still deferred (unchanged from v2): ClaudeBar pixel mascot (tray
shape+colour already carries state); animated sparklines (Unicode-only,
no Cairo); per-account notification preferences.

Also fixed under §5: the empty-state welcome card's ``activate`` handler
now calls ``on_open_dashboard`` (it was previously wired to a no-op,
rendering clickable-but-dead).

## Handoff — items the @fixer interprets

1. **Chroma split**: tray `#3a9d5d`/`#d4a72c`, popup v2's `#7AE582`/`#F5C36A`.
   Fixer decides naming — two dicts or one with a `surface` field.
2. **`↻` a11y**: §3 suggests an `accessible-label` override. Fixer picks
   custom `set_tooltip_text` or accepts Pango's literal glyph read-out.
3. **Empty-state clickability**: §5 keeps card clickable; fixer may demote
   to non-sensitive if a separate "Open Dashboard" button reads cleaner.
4. **Shape glyph fallback**: cascade `Cantarell` → `Adwaita Sans` →
   `Noto Sans`, or fall back to v2's letter+colour if `.notdef` shows.
5. **Light-theme accent table**: §7 ships an explicit lookup — pre-compute
   at module load or per-call, either fine.