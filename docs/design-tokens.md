# custats-linux — Design Tokens

Phase 4 visual contract for the GTK + AppIndicator3 UI. All decisions here are
deliberately shared across tray icon, popup menu, and main window so the user
gets one consistent visual language.

## 1. Status colours (light-on-dark, single palette)

Used on tray glyph, popup bars, status pills, dialog accents. Picked for AA
contrast on the default GTK dark surfaces (`#1e1e1e`–`#2a2a2a`) and to read
cleanly through the GNOME top-bar transparency layer.

| UsageStatus | Foreground | Weight | Label  | Notes                             |
|-------------|------------|--------|--------|-----------------------------------|
| `GOOD`      | `#7AE582`  | regular| OK     | Soft green; not neon.             |
| `CAUTION`   | `#F5C36A`  | regular| Watch  | Amber; reads as "heads up".       |
| `CRITICAL`  | `#FF7A6B`  | bold   | Warn   | Coral-red; pops without alarming. |
| `AT_LIMIT`  | `#FF4D4D`  | bold   | Limit  | Saturated red; reserved for >=100%.|
| `UNKNOWN`   | `#8A8F98`  | regular| —      | Muted slate; never confusable w/ green. |

Thresholds (already in `core/status.py`): `GOOD` < 70, `CAUTION` 70–89,
`CRITICAL` 90–99, `AT_LIMIT` >= 100. Status pills always render the label
plus the percentage so colour is never the only signal.

## 2. Icon language

The tray icon is one glyph rendered into AppIndicator's `icon-name` slot via a
`CairoSurface` from `Pango` (no asset files, no SVG). 16px is the only target.

**Provider glyphs** — single uppercase letter, regular weight. Chosen over
Unicode symbols because letters survive font fallback across GNOME / KDE / XFCE.

| Provider | Glyph | Why this letter                  |
|----------|-------|----------------------------------|
| `CLAUDE` | `C`   | First letter; unambiguous.       |
| `CODEX`  | `X`   | "C" taken; "X" reads as Codex.   |
| `GROK`   | `G`   | First letter; no conflict.       |
| `CURSOR` | `U`   | "C" taken; stylised as Cursor.   |

**Severity overlay rule** — colour IS the overlay. At 16px we do not prepend
`⚠` or append `!`; both add noise without signal. The worst account's
provider letter is drawn in the colour of the worst `UsageStatus` across all
accounts (computed via `state.compute_aggregate`). Weight follows section 1
(regular for GOOD/CAUTION, bold for CRITICAL/AT_LIMIT).

**Idle / "all clear" state** — render the literal glyph `…` (U+2026) in
`#8A8F98` whenever there are zero accounts OR every account's worst status is
GOOD/UNKNOWN. This says "nothing to worry about" without hiding the icon.

**Aggregate rule** — if multiple accounts exist, the icon shows the single
worst provider. The popup lists all of them, so the icon only needs to grab
attention.

## 3. Menu bar popup layout

~360px wide, vertical stack, dark surface. AppIndicator menu items are
`GtkMenuItem` subclasses.

**Per-account row** — header line + two stacked progress lines.

```
┌──────────────────────────────────────────────────────────┐
│  ◉ my-claude  ·  Claude Pro               [ Watch 78% ] │
│    5h  ████████░░░░░░░░░░  41%   resets in  3h 12m      │
│    7d  ███████████░░░░░░░  61%   pace  Risky            │
└──────────────────────────────────────────────────────────┘
```

- Header: provider dot (`◉` coloured by worst status) + alias + provider name.
  Right-aligned status pill uses section 1 colours.
- 5h line: label, thin bar (~180px), percent, reset countdown.
- 7d line: same shape, plus optional pace chip (`Healthy`/`Risky`/`Over`).
- If a window is `None` (e.g. Cursor has no 5h), the line is omitted entirely,
  not greyed out.
- Error state: header stays, bar lines are replaced by `! {error}` in
  `#FF7A6B`.

**Section separators** — a 1px `#3a3a3a` horizontal rule sits between account
rows when `len(accounts) > 1`. No separator before the first row or after the
last. Accounts are grouped by provider with a subtle sub-header only when the
user has 3+ accounts spanning 2+ providers (otherwise it's noise).

**Footer** — divider, then two menu items: `Settings…` (opens main window)
and `Quit custats`. `Settings` carries the keyboard accelerator hint
(`Ctrl+,`) in a muted style. Spacing above footer is one blank row.

**Empty state** — single menu item, full-width, with a Settings shortcut:

```
┌──────────────────────────────────────────────────────────┐
│   No accounts configured.                                │
│   Open Settings to add one.                       [ → ]  │
└──────────────────────────────────────────────────────────┘
```

The whole row is a clickable `GtkMenuItem` that opens the main window.

## 4. Main window (settings + history)

`GtkApplicationWindow`, ≥ 600×400, dark by default (auto in section 5). A
left-hand `GtkStackSidebar` with four tabs; the right pane is a `GtkStack`.

**Live** — the same data as the popup, as cards. Top-to-bottom per account:
provider header, 5h card (bar, %, status pill, reset time), 7d card (bar, %,
status pill, reset time, pace projection as a third bar overlay). Bottom of
the pane: `Refresh now` button + `Last polled: HH:MM:SS` muted text.

**Accounts** — `GtkListBox` of rows: alias, provider badge, created date,
last-seen date, active toggle. Toolbar above the list: `+ Add account…`,
`Import from macOS CUStats…` (deferred — wire up later). Per-row context
menu: `Edit alias`, `Deactivate`, `Delete…` (confirmation dialog).

**Settings** — vertical `GtkBox` of labelled rows (no nested tabs):

1. Poll interval — numeric stepper, 30–600s, default 60.
2. Notifications — switch: notify on CRITICAL / AT_LIMIT transitions.
3. Pace projection — switch: enable 7-day burn-rate projection.
4. Theme — radio: `Follow system` / `Force dark` / `Force light`. Default
   `Follow system`.
5. Data directory — read-only label + `Open folder` button.
6. Reset to defaults — destructive button, confirmation required.

**About** — centred column: product name, version (from `pyproject.toml`),
one-line description, license (MIT), project URL, `View logs` button. No
splash graphic — text only.

## 5. Refresh interval & typography

- Tray icon: 16×16 logical pixels, drawn into a `cairo.ImageSurface` then
  shipped to AppIndicator via `set_icon_full(icon_name, desc)`. The
  `icon-name` is a generated unique name; the actual pixels live in a
  in-memory pixbuf.
- Popup font: `Adwaita Sans` if present, else system sans. 13pt semibold for
  row headers, 11pt regular for body, 10pt for meta (reset countdown, pace).
  Mono fallback (`Adwaita Mono`) for the percent + countdown so digits align.
- Main window: same font scale +1pt (14pt headers, 12pt body) for desktop
  comfort.
- Colour theme: `auto` — follow GNOME's `org.gnome.desktop.interface
  color-scheme`; honour a manual override from Settings. Backgrounds:
  `#1e1e1e` (dark) / `#fafafa` (light); status colours from section 1 are
  unchanged in either mode because they were chosen for dark contrast and
  happen to remain legible on light backgrounds.
- Refresh cadence: no separate timer. The UI calls
  `poller.subscribe(callback)` on startup; every poll cycle re-renders the
  tray icon, popup (if open), and the Live tab. Poll interval itself is a
  user setting.

## 6. Accessibility

- Every status colour is paired with a text token: the percent number and
  the single-word label from section 1 appear in the same row. Colour alone
  never carries meaning.
- Tray icon sets an accessible description (`"custats — 1 critical, 2 OK"`)
  on every redraw; screen readers announce it on focus.
- Popup rows are real `GtkMenuItem` widgets — keyboard nav (Up/Down, Enter,
  Esc) comes for free from GTK. Each row's mnemonic is the first letter of
  the alias.
- Main window: standard GTK keyboard nav; `F6` jumps between sidebar and
  pane; `Ctrl+W` closes; `Ctrl+Q` quits the daemon.
- Focus rings are not disabled; respect the system `gtk-application-prefer-
  dark-theme` and high-contrast settings.
- All dialogs have a labelled `Cancel` action and confirm destructive
  actions (`Delete account…`, `Reset to defaults`) via a secondary modal.
