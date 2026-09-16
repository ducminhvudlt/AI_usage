# custats — Linux menu bar tracker

[![Linux](https://img.shields.io/badge/platform-Linux-blue)](#quick-install-ubuntu-2204)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](pyproject.toml)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

> Real-time Claude / Codex / Grok / Cursor **usage-limit** tracking, right in
> your panel. No Electron. No cloud. Just GTK.

<!-- Screenshots TODO: drop a tray-icon screenshot and a settings-window screenshot here. -->

---

## What it does

- 📊 **Real-time Claude 5-hour / 7-day** limit monitoring with live countdown
  timers.
- 🤖 **OpenAI Codex**, **xAI Grok**, and **Cursor** usage windows on the same
  tray icon — letter glyph rotates to the worst account.
- 👥 **Multi-account** support. Track several Claude Pro / Max / Team
  workspaces or Cursor plans side-by-side.
- ⏱️ **Live countdown timer** to the next 5-hour reset, with a pace chip
  (`Healthy` / `Risky` / `Over`) for the 7-day window.
- 🔐 **On-device encrypted credentials** (AES-GCM, key in
  `~/.config/custats/secret.key`). Nothing leaves your machine except
  provider-API requests for your own accounts.
- 🔔 **Desktop notifications** when an account crosses `CRITICAL` /
  `AT_LIMIT` and when it recovers (optional).
- 🎨 **Dark / light themes** that follow GNOME's `color-scheme` setting;
  manual override available in Settings.

---

## Quick install (Ubuntu 22.04)

```bash
git clone https://github.com/custats/custats-linux.git
cd custats-linux
./install.sh
```

That's it. The script installs GTK + AppIndicator system packages (via
`sudo apt-get`), creates a user-local venv at
`~/.local/share/custats/venv`, symlinks the `custats` binary into
`~/.local/bin/`, drops a `.desktop` file into your XDG autostart dir, and
(when systemd user sessions are available) enables
`~/.config/systemd/user/custats.service`.

### Alternative: install from PyPI

```bash
python3 -m venv ~/.local/share/custats/venv
~/.local/share/custats/venv/bin/pip install --upgrade pip
~/.local/share/custats/venv/bin/pip install custats
ln -sf ~/.local/share/custats/venv/bin/custats ~/.local/bin/custats
```

Then run `custats install-service` to drop the systemd unit and
`~/.config/systemd/user/custats.service`, or skip that and rely on the XDG
autostart entry (see below).

> **PATH hint**: `~/.local/bin` is on PATH for login shells by default on
> Ubuntu. If `custats: command not found`, add
> `export PATH="$HOME/.local/bin:$PATH"` to your `~/.bashrc`.

---

## Add your first account

```bash
custats add --provider claude --alias work \
            --session-key "<value from claude.ai cookies>"
```

Repeat for the other providers with the right credential flag — full
walk-through with screenshots in [docs/setup-guide.md](docs/setup-guide.md):

| Provider | Required flag                                         |
|----------|--------------------------------------------------------|
| Claude   | `--session-key <value>`                                |
| Codex    | `--cookie <cookie-header>` *or* `--auth-json <path>`   |
| Grok     | `--auth-json ~/.grok/auth.json`                        |
| Cursor   | `--cookie <workos-cursor-cookie>`                      |

---

## Run it

Foreground (visible terminal, easiest for first run):

```bash
custats run
```

Background, persistent across reboots:

```bash
systemctl --user start custats.service    # start now
systemctl --user status custats.service  # check on it
journalctl --user -u custats.service -f  # follow logs
```

> If `systemctl --user` complains "Failed to connect to bus", your desktop
> session doesn't have systemd user services (some XFCE / i3 setups). The
> XDG `.desktop` autostart entry is the fallback — custats will start next
> time you log in.

---

## CLI reference

| Subcommand            | What it does                                                |
|-----------------------|-------------------------------------------------------------|
| `run`                 | Start the menu-bar app (foreground).                        |
| `add`                 | Add a new provider account (interactive or `--flag` driven).|
| `login`               | Browser sign-in via OAuth device-code (Codex + ChatGPT; Cloudflare-gated — cookie paste is the reliable fallback). |
| `remove`              | Delete an account by id (`custats list` to find ids).       |
| `onboard`             | First-run wizard: detect installed CLIs + walk through setup.|
| `list`                | Print a table of tracked accounts.                          |
| `doctor`              | Preflight environment checks; `--json` for scripts.         |
| `refresh`             | Rotate OAuth refresh tokens (Codex + ChatGPT).              |
| `install-service`     | Drop + enable the systemd user unit.                        |
| `uninstall-service`   | Disable + remove the systemd user unit.                     |
| `show-config`         | Print the resolved configuration.                           |

Use `custats <subcommand> --help` for flags.

---

## Configuration

`~/.config/custats/config.toml` (created on first run) — all keys are
optional; defaults shown:

```toml
refresh_interval_seconds = 60      # 30..600
notify_threshold_percent = 80      # percent; CRITICAL/AT_LIMIT fires here
notify_on_recovery       = true
pace_enabled             = true
theme                    = "auto"   # "auto" | "dark" | "light"

[show_in_menu_bar]
claude  = true
codex   = true
grok    = true
cursor  = true
```

Edit the file directly, or use the **Settings** tab in the main window.

---

## Storage

| Path                                      | What lives there                              |
|-------------------------------------------|------------------------------------------------|
| `~/.config/custats/config.toml`           | User preferences (TOML).                       |
| `~/.config/custats/secret.key`            | 256-bit AES-GCM key, file mode `0600`.         |
| `~/.local/share/custats/state.db`         | SQLite database. Account credentials are stored as AES-GCM ciphertext + nonce. |
| `~/.config/systemd/user/custats.service`  | systemd user unit (only if you ran `install-service`). |
| `${XDG_CONFIG_HOME}/autostart/custats.desktop` | XDG autostart entry (fallback).           |

To migrate between machines: copy `config.toml` + `secret.key` +
`state.db`. Without all three the credentials are unrecoverable.

---

## Uninstall

```bash
custats uninstall-service        # disable + remove the systemd unit
# remove the venv + autostart entry:
rm -rf ~/.local/share/custats
rm ~/.config/autostart/custats.desktop
# (optional) remove config + secrets:
rm -rf ~/.config/custats
```

Re-running `./install.sh` is always safe — it's idempotent and will recreate
a missing venv / autostart entry / service unit.

---

## Known limitations

- **No visual GTK verification in CI.** The dev/CI boxes lack
  `girepository-2.0` typelibs, so the UI test suite runs against
  `MagicMock` GTK bindings (per `docs/design-tokens-v3.md` §9). On a
  GTK-capable machine you can run the opt-in real-GTK smoke suite:
  `CUSTATS_GTK_TESTS=1 pytest tests/ui/test_gtk_integration.py`.
- **Cloudflare blocks `custats login` device-flow requests.** OpenAI's
  device-code endpoint (Codex + ChatGPT) sits behind bot protection that
  rejects the stock `httpx` TLS fingerprint — a "Just a moment…" 403 can
  happen regardless of headers. **Cookie paste is the reliable path:**
  `custats add --provider codex --cookie '<full Cookie header>'` hits the
  auth-only `/backend-api/usage` endpoint, which is not
  Cloudflare-gated. `custats login` prints this warning before it starts.
- **Claude / Grok / Cursor have no public OAuth device-code endpoint**, so
  browser sign-in is impossible for them — cookie / session-key paste is
  the only supported path (`custats add`, see `docs/setup-guide.md`).
- Status-change animations and the "Open data folder" popup footer were
  **deferred** per `docs/design-tokens-v3.md` §10 but have since shipped:
  worsening severity now pulses the tray icon via AppIndicator's
  `ATTENTION` status, and the popup has an "Open data folder" item that
  opens `~/.local/share/custats`.
- Per-account notification preferences and animated sparklines (two more
  former §10 deferrals) have also shipped: toggle notifications per
  account in the Accounts tab (`Notify: on/off`), and the popup pace
  sparkline now animates in two Unicode-only frames over your **real**
  usage history (last ten 7-day readings from `state.db`) — no Cairo, no
  timers. Only the ClaudeBar pixel mascot remains deferred.

---

## Troubleshooting

**"GTK not available" / tray icon never appears.** Install the GObject
introspection bindings:

```bash
sudo apt-get install -y \
    libgtk-3-1 gir1.2-gtk-3.0 gir1.2-appindicator3-0.1 libnotify-bin
```

Run `custats doctor` for a full pre-flight report.

**Credentials expired / 401 from the provider.** Re-add the account with the
fresh credential:

```bash
custats add --provider claude --alias work --session-key "<new-value>"
# or remove first to avoid duplicates:
custats remove --account-id <id>
```

**Service not starting.** Check the journal:

```bash
journalctl --user -u custats.service -n 100 --no-pager
```

The most common cause is `~/.local/bin/custats` missing — re-run
`./install.sh`.

**Tray icon stuck after logout/login.** Run
`systemctl --user restart custats.service` or log out and back in once; the
XDG autostart entry will re-launch it.

**`custats: command not found`.** Add `~/.local/bin` to PATH (see *Quick
install* above), or invoke the binary directly:
`~/.local/share/custats/venv/bin/custats`.

---

## Development

```bash
git clone https://github.com/custats/custats-linux.git
cd custats-linux
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[test]"
pytest                                    # runs the full suite
pytest tests/test_packaging.py -v         # packaging-specific
```

Project layout:

```
src/custats/
    cli.py           # argparse + subcommand handlers
    poller.py        # async polling loop
    notifier.py      # desktop notifications
    core/            # pure data models, config, status, pace
    providers/       # one adapter per AI service
    storage/         # SQLite + AES-GCM
    ui/              # GTK tray icon, popup, main window
packaging/
    custats.service  # systemd user unit
    custats.desktop  # XDG autostart entry
install.sh            # one-shot installer
docs/
    design-tokens.md # visual contract for the GTK UI
    setup-guide.md   # per-provider credential extraction walkthrough
```

Before opening a PR, please:

1. Run `pytest` — must stay green.
2. Re-read [docs/design-tokens.md](docs/design-tokens.md) and respect the
   status colours / icon language if you're touching the UI.
3. Don't add new runtime dependencies to `pyproject.toml` without a
   discussion — the install footprint matters.

---

## Credits

custats-linux is a **Linux port** of [CUStats](https://custats.info/) by
[@bruceoutdoors](https://github.com/bruceoutdoors) and contributors. CUStats
targets macOS; this project re-implements the same idea natively on Linux
using GTK + AppIndicator3, and ships its own provider adapters. Design
tokens, provider URL list, and the original FAQ format are informed by
CUStats' source.

Provider names and trademarks (Claude, Codex, Grok, Cursor) belong to their
respective owners. custats-linux is unaffiliated with any of them; it talks
to their public web APIs using your own credentials.

Released under the MIT license.
