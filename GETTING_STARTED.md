# Getting Started — `custats` command reference

This is a focused command reference for **all 11 supported providers**. For
installation see [`README.md`](README.md); for credential-extraction walkthroughs
(see → `docs/setup-guide.md`).

> **All commands assume `custats` is on your PATH** (the installer puts it in
> `~/.local/bin/custats`). If `custats: command not found`, either re-run
> `./install.sh` or invoke the venv binary directly:
> `~/.local/share/custats/venv/bin/custats <subcommand>`.

---

## 0. First-run setup

If you just installed `custats` and want a guided tour:

```bash
# Detect installed CLIs and walk through provider setup
custats onboard
```

The wizard prints which provider CLIs it found on `$PATH` (Claude Code,
Codex CLI, `gh`, Grok CLI, Kimi CLI, …), offers a one-shot setup per
detected provider (the matching `custats login` / `custats add` command
runs in your terminal — including the browser OAuth flow), and exits
cleanly. In non-interactive contexts (CI, scripts) it prints a one-line
summary and exits 0. Then run `custats run` to start the menu bar app.

---

## 1. The 30-second quick start

Pick **one** provider you have a subscription for. The Codex / ChatGPT path is
the easiest because the browser does the auth — no cookie-pasting required.

```bash
# 1. Browser OAuth (Codex + ChatGPT only)
custats login --provider codex  --alias work
# → prints a URL + 8-char code; open the URL in any browser, type the code,
#   approve; custats writes ~/.codex/auth.json and adds the account.

# 2. Cookie paste (any provider)
custats add --provider claude --alias work --session-key 'sk-ant-…'
# → encrypted credential blob stored in ~/.local/share/custats/state.db

# 3. Run it
custats run
```

You'll see the tray icon appear with your usage percentage. Click the icon
for a popup with details; click "Open Dashboard" for the full window.

---

## 2. Provider-by-provider commands

### 2.1 Claude (Anthropic)

| Auth method | Command |
|---|---|
| **Cookie (only path)** | `custats add --provider claude --alias <name> --session-key '<value>'` |

How to get the `sessionKey` value:

1. Open <https://claude.ai> in a browser, log in
2. Open DevTools (`F12` / `Cmd+Option+I`)
3. Tab **Application** → **Cookies** → `https://claude.ai`
4. Filter: `sessionKey` → copy the **Value** column (starts with `sk-ant-…`)
5. Paste it as the `--session-key` argument (single-quote the value; it contains special characters)

For multi-account, run `custats add` again with a different `--alias`:

```bash
custats add --provider claude --alias work  --session-key 'sk-ant-work…'
custats add --provider claude --alias personal --session-key 'sk-ant-personal…'
```

**Refresh:** Claude sessionKey cookies expire every few weeks. Re-run
`custats add --provider claude --alias <same-alias> --session-key '<new>'`
to update just the credential blob (history is preserved).

---

### 2.2 ChatGPT (OpenAI)

| Auth method | Command |
|---|---|
| **Browser OAuth (recommended)** | `custats login --provider chatgpt --alias <name>` |
| **Cookie paste** | `custats add --provider chatgpt --alias <name> --cookie '<full Cookie header>'` |
| **Auth.json paste** | `custats add --provider chatgpt --alias <name> --auth-json <path>` |

**Browser OAuth (recommended):**

```bash
custats login --provider chatgpt --alias work
```

The CLI prints:
```
custats: opening browser sign-in for chatgpt
Visit: https://auth.openai.com/codex/device
```

After the device-code request returns:
```
Enter code: ABCD-EFGH
(expires in 10:00 — press Ctrl-C to cancel)
```

Open the URL in any browser, enter the code, approve ChatGPT in the consent
screen. custats writes `~/.chatgpt/auth.json` and inserts the account row.

> **Note:** The browser sign-in (`custats login`) hits OpenAI's device-code
> endpoint, which is gated by Cloudflare's bot-protection. If you see
> HTTP 403 with a "Just a moment…" page, Cloudflare has blocked the
> request — your IP or the codex-cli User-Agent version is on their
> deny-list. Fall back to **cookie paste** (above) which hits the
> auth-only `/backend-api/usage` endpoint and is not Cloudflare-protected.

> **One login covers both products.** OpenAI's `device_code_hint` is set to
> `["codex", "chatgpt"]`, so a single browser approval grants tokens for both.
> You can then run `custats login --provider codex --alias <other-alias>`
> *without* re-authing in the browser — the same cached token works.

**Cookie paste:** in DevTools → Network → click any `chatgpt.com/api/...`
request → Headers → copy the entire `Cookie:` header value. The minimum
required cookie is `__Secure-next-auth-session-token=<value>` but a full
header (all cookies) is safer.

**Refresh:** automatic on 401. When `access_token` expires, the next poll
returns 401 → custats refreshes via `refresh_token` → persists to
`~/.chatgpt/auth.json` → retries the poll. No user action needed.

Force-refresh manually:

```bash
custats refresh --account-id <id>     # one account
custats refresh --all                  # every refreshable account
```

---

### 2.3 Codex (OpenAI)

| Auth method | Command |
|---|---|
| **Browser OAuth (recommended)** | `custats login --provider codex --alias <name>` |
| **Cookie paste** | `custats add --provider codex --alias <name> --cookie '<full Cookie header>'` |
| **`~/.codex/auth.json` paste** | `custats add --provider codex --alias <name> --auth-json ~/.codex/auth.json` |

**Browser OAuth (recommended):**

```bash
custats login --provider codex --alias code
```

Same flow as ChatGPT. Visit `https://auth.openai.com/codex/device`, enter the
short code, approve. custats writes `~/.codex/auth.json` (the same file
`codex login` uses — so existing `codex login` users can skip this step and
just point custats at the existing file).

> **Note:** The browser sign-in (`custats login`) hits OpenAI's device-code
> endpoint, which is gated by Cloudflare's bot-protection. If you see
> HTTP 403 with a "Just a moment…" page, Cloudflare has blocked the
> request — your IP or the codex-cli User-Agent version is on their
> deny-list. Fall back to **cookie paste** (above) which hits the
> auth-only `/backend-api/usage` endpoint and is not Cloudflare-protected.

**Auth.json paste:** if you've already done `codex login`, the token is at
`~/.codex/auth.json`. Just point custats at it:

```bash
custats add --provider codex --alias code --auth-json ~/.codex/auth.json
```

This is the lowest-friction path for users who already use the Codex CLI.

**Refresh:** automatic on 401 (same path as ChatGPT). `~/.codex/auth.json` is
updated in-place; your next `codex login` will see the refreshed token too.

---

### 2.4 Grok (xAI)

| Auth method | Command |
|---|---|
| **`~/.grok/auth.json` paste (only path)** | `custats add --provider grok --alias <name> --auth-json <path>` |

Grok does **not** publish a public OAuth device-code endpoint. The supported
auth path is the token file produced by the official Grok CLI:

```bash
# 1. Install + sign in to the Grok CLI (one-time)
#    https://docs.x.ai/docs/guides/authentication
grok login
#    → writes ~/.grok/auth.json with access_token + refresh_token

# 2. Point custats at it
custats add --provider grok --alias mygrok --auth-json ~/.grok/auth.json
```

**Refresh:** custats **never rotates** the Grok refresh_token, by design
(mirrors CUStats). When `access_token` expires, the next poll fails with
`auth rejected; re-paste credential`. Re-run `grok login` to refresh the
file, then re-add with `--auth-json ~/.grok/auth.json` (same alias).

**Why no auto-refresh?** xAI's refresh-token endpoint isn't publicly
documented, so custats can't safely rotate without breaking your CLI
session. The CLI refreshes on demand; you re-add. (Same trade-off as
CUStats Mac.)

---

### 2.5 Cursor

| Auth method | Command |
|---|---|
| **WorkosCursor cookie (only path)** | `custats add --provider cursor --alias <name> --cookie '<WorkosCursor value>'` |

Cursor cookies **do not refresh** — there is no API for it. Re-paste when
the cookie expires (typically weeks). The adapter surfaces a friendly
`re-paste a fresh WorkosCursor value` message on 401.

How to get the cookie:

1. Open <https://cursor.com> in a browser, log in via your normal method
   (Google / GitHub / email)
2. DevTools → Application → Cookies → `https://cursor.com`
3. Find `WorkosCursor` → copy its Value
4. Pass to `--cookie`

```bash
custats add --provider cursor --alias cur --cookie 'WorkosCursor=<value>'
```

---

### 2.6 Gemini (Google)

| Auth method | Command |
|---|---|
| **API key (only path)** | `custats add --provider gemini --alias <name> --api-key '<AIzaSy-…>'` |

Gemini does not publish a per-account usage / quota endpoint. The adapter
verifies the key by hitting the public `models.list` endpoint and returns a
snapshot with no window percentages — **no error in the tray means the key
is valid.**

How to get the key:

1. Open <https://aistudio.google.com> and sign in
2. **Get API key** → **Create API key** (or copy an existing one)
3. Paste the value (starts with `AIzaSy`) as `--api-key`

```bash
custats add --provider gemini --alias work --api-key 'AIzaSy-…'
```

**Refresh:** manual — re-run `add` with the new key when you revoke /
replace. The adapter only reads from the public `generativelanguage.googleapis.com`
endpoint, so rotating the key in AI Studio does not break an existing
account until you re-add it.

---

### 2.7 OpenRouter

| Auth method | Command |
|---|---|
| **API key (only path)** | `custats add --provider openrouter --alias <name> --api-key '<sk-or-v1-…>'` |

OpenRouter exposes a per-key credit endpoint (`/api/v1/auth/key`) that
returns cents used vs cents limit. The adapter surfaces that as the
5-hour percentage and the remaining dollar credit in the popup's
*Credits* row. It never invokes paid inference — read-only.

How to get the key:

1. Open <https://openrouter.ai> and sign in
2. **Keys** → **Create Key**
3. Paste the value (starts with `sk-or-v1-`) as `--api-key`

```bash
custats add --provider openrouter --alias or --api-key 'sk-or-v1-…'
```

**Refresh:** manual — re-run `add` with the new key when the old one is
revoked (a 401 will appear in the tray until you do).

---

### 2.8 DeepSeek

| Auth method | Command |
|---|---|
| **API key (only path)** | `custats add --provider deepseek --alias <name> --api-key '<sk-…>'` |

DeepSeek's public API only exposes a `/user/balance` endpoint (USDT
credit remaining). The adapter surfaces that as the popup's *Credits*
row. No percentage windows are populated.

How to get the key:

1. Open <https://platform.deepseek.com> and sign in
2. **API Keys** → **Create new secret**
3. Paste the value (starts with `sk-`) as `--api-key`

```bash
custats add --provider deepseek --alias ds --api-key 'sk-…'
```

**Refresh:** manual — re-run `add` with the new key when you rotate.

---

### 2.9 Mistral AI

| Auth method | Command |
|---|---|
| **API key (only path)** | `custats add --provider mistral --alias <name> --api-key '<value>'` |

Mistral La Plateforme exposes a `/v1/users/me/usage/balance` endpoint
that reports remaining credit in cents. The adapter surfaces that as
the popup's *Credits* row. No percentage windows are populated — the
public API doesn't publish per-window quotas.

How to get the key:

1. Open <https://console.mistral.ai> and sign in
2. **API Keys** → **Create new key**
3. Paste the value as `--api-key`

```bash
custats add --provider mistral --alias mis --api-key '<value>'
```

**Refresh:** manual — re-run `add` with the new key when the old one
is revoked.

---

### 2.10 Kimi (Moonshot AI)

| Auth method | Command |
|---|---|
| **API key (only path)** | `custats add --provider kimi --alias <name> --api-key '<value>'` |

Moonshot / Kimi's billing endpoint (`/v1/dashboard/billing/credit`)
returns the remaining credit in cents. The adapter surfaces that as
the popup's *Credits* row. No percentage windows are populated.

How to get the key:

1. Open <https://platform.moonshot.cn> and sign in (or run
   `kimi login` from the Kimi CLI — same key works for both)
2. **API Keys** → **Create new key**
3. Paste the value as `--api-key`

```bash
custats add --provider kimi --alias kimi --api-key '<value>'
```

**Refresh:** manual — re-run `add` with the new key when the old one
is revoked.

---

### 2.11 GitHub Copilot

| Auth method | Command |
|---|---|
| **Browser OAuth (recommended)** | `custats login --provider copilot --alias <name>` |

GitHub Copilot uses GitHub's public RFC 8628 device-code endpoint
(`github.com/login/device/code`). The CLI walks through the same flow
as `gh copilot`: print a URL + short code, poll until you approve in
the browser, persist the token bundle to
`$XDG_CONFIG_HOME/custats/copilot.json`, add the account.

```bash
custats login --provider copilot --alias pro
# Visit https://github.com/login/device, enter the code, approve.
```

The CLI prints:
```
custats: opening browser sign-in for copilot
Visit: https://github.com/login/device
```

After the device-code request returns:
```
Enter code: WXYZ-9876
(expires in 14:59 — press Ctrl-C to cancel)
```

Open the URL in any browser, sign in to GitHub if prompted, enter the
code, approve the requested scope (`copilot read:user`). custats
writes `copilot.json` and inserts the account row.

**What the menu bar shows:** the consumed percentage from your most
relevant quota bucket — by default `monthly_ide_chat` (IDE chat
quota). The adapter falls back to `monthly_agent_chat`, then
`monthly_ide_completions` if the preferred bucket is missing.
Unlimited plans surface as 0%.

**Subscription:** subscribe at <https://github.com/features/copilot>
(Business / Pro / Enterprise / Individual tiers).

**Refresh:** automatic on 401. When the access_token expires, the
adapter calls GitHub's `login/oauth/access_token` endpoint with
`grant_type=refresh_token`, persists the new bundle to `copilot.json`,
and retries the poll exactly once.

Force-refresh manually (alongside every other refreshable account):
```bash
custats refresh --all   # Codex + ChatGPT + GitHub Copilot
```

---

## 3. Listing / inspecting accounts

```bash
custats list                       # show all active accounts
custats list --all                 # include inactive (soft-deleted) accounts
custats show-config                # show resolved config + paths
custats doctor                     # preflight checks (Python, GTK, DBus, etc.)
```

Example output of `custats list`:
```
ALIAS          PROVIDER   ID                                ACTIVE   LAST SEEN
work           claude     a3f1b2c4d5e6f7a8b9c0d1e2f3a4b5c6    yes      2 min ago
mygrok         grok       b4c2d3e5f6a7b8c9d0e1f2a3b4c5d6e7    yes      5 min ago
code           codex      c5d3e4f6a7b8c9d0e1f2a3b4c5d6e7f8    yes      1 min ago
```

---

## 4. Removing accounts

```bash
custats list                              # find the ID
custats remove --account-id <id>          # soft-delete (history kept)
custats remove --account-id <id> --purge  # hard-delete (drops usage history)
```

Removal is reversible — re-adding the same provider with the same alias
generates a new account ID, but the credential blob can be re-imported from
any backing file (cookie, auth.json).

---

## 5. Refreshing tokens

Codex + ChatGPT + GitHub Copilot auto-refresh on 401 — no action
needed for normal use.
For force-refresh (after a long idle, or to verify the auth.json is current):

```bash
custats refresh --account-id <id>     # one account (finds its auth.json + refreshes)
custats refresh --all                  # every account whose provider supports refresh
```

Grok + Claude + Cursor + the API-key providers are skipped by `--all`
(they have no refresh path).

---

## 6. Running the menu bar

```bash
# Foreground (terminal)
custats run

# Background via systemd user service (preferred)
systemctl --user start custats.service
systemctl --user status custats.service     # check status
journalctl --user -u custats.service -f     # live logs

# Re-enable autostart after a reinstall
./install.sh
```

To stop the background service:
```bash
systemctl --user stop custats.service
```

---

## 7. Configuration

`~/.config/custats/config.toml`:

```toml
refresh_interval_seconds = 60      # poll cadence (10..600)
notify_threshold_percent = 80      # threshold for "approaching limit" notification
notify_on_recovery = true          # notify when usage drops back below threshold
pace_enabled = true                # show weekly pace projection
theme = "auto"                      # "auto" | "light" | "dark"

[show_in_menu_bar]
claude = true
chatgpt = true
codex = true
copilot = true
gemini = true
grok = true
openrouter = true
deepseek = true
cursor = true
mistral = true
kimi = true
```

Edit and save — custats picks up changes on next poll (no restart).

---

## 8. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Tray icon shows `…` (idle) | No accounts OR every account is GOOD/UNKNOWN | `custats list` to confirm; if empty, `custats add …` |
| Popup shows `auth rejected; re-paste credential` | Session cookie expired | Re-run `custats add` with fresh cookie (same alias keeps history) |
| Popup shows `rate limited (awaiting retry)` | Provider 429'd | Wait — the poller backs off automatically |
| `custats run` exits with code 3 | GTK / AppIndicator3 missing | `sudo apt install gir1.2-gtk-3.0 gir1.2-appindicator3-0.1` |
| `custats run` exits silently | Already running (one tray app per session) | Check `pgrep -af 'custats run'`; kill the duplicate |
| Browser login hangs | Network blocked to `auth.openai.com` | `curl -v https://auth.openai.com` from the same network to debug |
| `custats refresh` returns non-zero | Token endpoint 500 (transient) | Wait + retry; if persistent, the provider's auth is down |
| `custats doctor` shows "secret key not present" | First-run key generation didn't write | Delete `~/.config/custats/secret.key` and re-run (regenerates; existing accounts become unreadable) |

For deeper diagnostics:

```bash
# Verbose tracing
RUST_LOG=custats=debug custats run 2>&1 | head -50
```

(Yes the env var name is misleading — it's the same convention used by
CUStats Mac for trace logging.)

---

## 9. Cross-references

| Need | See |
|---|---|
| Install on Ubuntu 22.04 | [`README.md`](README.md) |
| Step-by-step cookie extraction (with screenshots in the upstream CUStats FAQ) | [`docs/setup-guide.md`](docs/setup-guide.md) |
| Per-provider response-field mapping (best-effort; may need swapping) | `src/custats/providers/<provider>.py` (top-of-file URL constant) |
| Visual design tokens (colours, glyphs, popup layout) | [`docs/design-tokens.md`](docs/design-tokens.md) |
| Deepwork progress (project state, deferred items) | [`.slim/deepwork/custats-linux.md`](.slim/deepwork/custats-linux.md) |

---

## 10. One-page cheat sheet

```bash
# ── Codex + ChatGPT (browser OAuth) ─────────────────────────────
custats login --provider codex  --alias work      # → ~/.codex/auth.json
custats login --provider chatgpt --alias mychat   # → ~/.chatgpt/auth.json
                                                  # 1 browser approval covers both

# ── GitHub Copilot (browser OAuth) ─────────────────────────────
custats login --provider copilot --alias pro      # → ~/.config/custats/copilot.json

# ── Claude (cookie paste) ────────────────────────────────────────
custats add --provider claude --alias work --session-key 'sk-ant-…'

# ── Grok (auth.json from official CLI) ──────────────────────────
grok login                                         # one-time: produces ~/.grok/auth.json
custats add --provider grok --alias mygrok --auth-json ~/.grok/auth.json

# ── Cursor (cookie paste, no refresh) ────────────────────────────
custats add --provider cursor --alias cur --cookie 'WorkosCursor=…'

# ── Gemini / OpenRouter / DeepSeek / Mistral / Kimi (API key) ─────
custats add --provider gemini     --alias mygem --api-key 'AIzaSy-…'
custats add --provider openrouter --alias myor  --api-key 'sk-or-v1-…'
custats add --provider deepseek   --alias myds  --api-key 'sk-…'
custats add --provider mistral    --alias mymis --api-key '<value>'
custats add --provider kimi       --alias mykimi --api-key '<value>'

# ── Inspect / manage ─────────────────────────────────────────────
custats list                                       # active accounts
custats list --all                                 # including soft-deleted
custats show-config                                # resolved paths + values
custats doctor                                     # preflight checks
custats refresh --all                              # force-refresh all (Codex + ChatGPT only)
custats remove --account-id <id>                   # soft-delete
custats remove --account-id <id> --purge           # hard-delete

# ── Run ──────────────────────────────────────────────────────────
custats run                                        # foreground
systemctl --user start custats.service             # background (autostart)
journalctl --user -u custats.service -f            # live logs
```
