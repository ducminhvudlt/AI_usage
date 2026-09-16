# setup-guide.md — extracting credentials for each provider

custats reads your provider usage via the same web APIs your browser talks
to. To do that it needs the cookies / tokens your browser sends when you
log in. This page walks through where to find them for each supported
provider, mirroring the format used by the original [CUStats FAQ](https://custats.info/).

> ⚠️ **Security.** These credentials give full access to your provider
> account. custats stores them **encrypted at rest** with AES-GCM (key in
> `~/.config/custats/secret.key`, mode `0600`) — but treat the value as
> sensitive regardless. If you later revoke the session (logging out from
> the provider invalidates it), re-run the `add` command with a fresh
> value.

---

## 0. Onboarding wizard

If you just installed `custats` and want a guided tour of which
provider CLIs you already have on `$PATH`:

```bash
custats onboard
```

The wizard prints a "Detected CLIs" section (e.g. `claude`, `codex`,
`gh`, `grok`, `kimi`, …) and offers a one-shot setup per detected
provider. For each one, the matching `custats login` or `custats add`
command is invoked in your terminal — including the browser OAuth flow
for Codex / ChatGPT / Copilot. After setup finishes, run `custats run`
to start the menu bar app.

The wizard is non-destructive: every command it runs is a regular
`custats login` / `custats add` invocation that you could run by hand.
In non-interactive contexts (CI, scripts) it prints a one-line summary
and exits 0.

---

## 1. Claude

Provider `claude`. Single credential: a session-key cookie.

1. Open https://claude.ai in your browser and **sign in**.
2. Open **Developer Tools** (`F12` or `Ctrl+Shift+I`) → **Application**
   tab → **Cookies** → `https://claude.ai`.
3. Find the row whose **Name** is `sessionKey`. Copy the **Value** column
   (a long base64 string, no quotes).
4. Run:
   ```bash
   custats add --provider claude --alias work --session-key "<paste>"
   ```

If the cookie expires (Claude rotates it after long idle periods), repeat
steps 2–4.

---

## 2. Codex (cookie approach)

Provider `codex`. Works if you use Codex from the browser at
chatgpt.com.

1. Open https://chatgpt.com and **sign in**.
2. Open **Developer Tools** → **Network** tab.
3. Make any Codex request (e.g. send "hi"). Find the request to
   `https://chatgpt.com/backend-api/...`.
4. In the request headers, copy the **entire** value of the `Cookie:`
   header (long string, contains `__Secure-next-auth.session-token=...` and
   friends).
5. Run:
   ```bash
   custats add --provider codex --alias personal --cookie "<paste>"
   ```

> The whole header is needed; custats uses multiple cookies inside it.

---

## 3. Codex (auth.json approach)

Provider `codex`. Preferred when you also use the Codex CLI and have
already logged in locally.

1. Make sure the Codex CLI is installed and you have run `codex login` at
   least once.
2. Locate the auth file:
   ```bash
   ls ~/.codex/auth.json
   ```
3. Run:
   ```bash
   custats add --provider codex --alias work --auth-json ~/.codex/auth.json
   ```

custats reads the JSON object and extracts `access_token` /
`refresh_token` / `id_token` as needed.

---

## 4. Grok

Provider `grok`. Same pattern as Codex's auth.json.

### From the Grok CLI

1. Install the Grok CLI and run `grok login`.
2. Verify the auth file exists:
   ```bash
   ls ~/.grok/auth.json
   ```
3. Add the account:
   ```bash
   custats add --provider grok --alias xai --auth-json ~/.grok/auth.json
   ```

### Manual (paste tokens directly)

If you have already obtained an `access_token` / `refresh_token` pair (for
example from the X developer portal), you can build a tiny JSON file:

```json
{
  "access_token":  "<value>",
  "refresh_token": "<value>",
  "client_id":     "<value>",
  "expires_at":    "2026-12-01T00:00:00Z"
}
```

Save it as `grok.json`, then:

```bash
custats add --provider grok --alias manual --auth-json ./grok.json
```

Delete the temporary file afterwards — custats has its own encrypted copy.

---

## 5. Cursor

Provider `cursor`. Single cookie value.

1. Open https://cursor.com and **sign in**.
2. Open **Developer Tools** → **Application** → **Cookies** →
   `https://cursor.com`.
3. Find the row whose **Name** is `WorkosCursor`. Copy the **Value**
   column.
4. Run:
   ```bash
   custats add --provider cursor --alias pro --cookie "<paste>"
   ```

---

## 6. ChatGPT

Provider `chatgpt`. Tracks ChatGPT Plus / Team / Pro **subscription** usage
(distinct from Codex, which is the coding product at the same backend).
Plus users see three windows:

- 5-hour rolling window (per-message-tier usage; may be absent for free users)
- 7-day weekly cap
- Monthly message cap (the primary visible limit for Plus)

### 6a. ChatGPT — browser sign-in (recommended)

OpenAI publishes the same `https://auth.openai.com/codex/device` endpoint
for both Codex and ChatGPT — the difference is the `device_code_hint`
payload. `custats login --provider chatgpt` sends `["codex", "chatgpt"]`,
so a single browser approval grants tokens for **both** products.

```bash
custats login --provider chatgpt --alias mychat
# Visit https://auth.openai.com/codex/device, enter the code, approve both
# Codex AND ChatGPT in the consent screen. custats writes the token to
# ~/.chatgpt/auth.json and adds the account.
```

> **One login covers both products.** Because the same token works for
> Codex and ChatGPT, you can re-run `custats login --provider codex`
> (or `--provider chatgpt`) without re-authing in the browser — the
> token is cached on the device.

> **Note:** The browser sign-in (`custats login`) hits OpenAI's device-code
> endpoint, which is gated by Cloudflare's bot-protection. If you see
> HTTP 403 with a "Just a moment…" page, Cloudflare has blocked the
> request — your IP or the codex-cli User-Agent version is on their
> deny-list. **Cookie paste is the reliable path** (below): it hits the
> auth-only `/backend-api/usage` endpoint and is not Cloudflare-protected.
> `custats login` also prints this warning before it starts.

### 6b. ChatGPT — cookie paste (manual fallback)

Use this if you'd rather skip the browser flow.

1. Open https://chatgpt.com and **sign in**.
2. Open **Developer Tools** → **Network** tab.
3. Make any chat request (e.g. send "hi"). Find the request to
   `https://chatgpt.com/backend-api/...`.
4. In the request headers, copy the **entire** value of the `Cookie:`
   header (long string, contains `__Secure-next-auth.session-token=...` and
   friends).
5. Run:
   ```bash
   custats add --provider chatgpt --alias personal --cookie "<paste>"
   ```

> ChatGPT does not currently ship a CLI; if you only have an OAuth
> `auth.json` (forward-compat with future ChatGPT login flows), pass it via
> `--auth-json /path/to/auth.json` instead.

---

## 7. Gemini

Provider `gemini`. Tracks Google AI Studio API key validity. Gemini
does not publish a per-account usage / quota endpoint, so the adapter
verifies the key by hitting the public `models.list` endpoint and
returns a snapshot with no window percentages — **"no error in the
tray" means the key is valid.**

1. Open <https://aistudio.google.com> in your browser and **sign in**.
2. Click **Get API key** → **Create API key** (or copy an existing
   one). The value is a long string starting with `AIzaSy`.
3. Run:
   ```bash
   custats add --provider gemini --alias work --api-key "AIzaSy-…"
   ```

> The same key works against the public `generativelanguage.googleapis.com`
> endpoint, which custats polls. Keys do not auto-rotate; re-run
> `custats add --provider gemini --alias work --api-key "<new>"` when
> you revoke / replace.

---

## 8. OpenRouter

Provider `openrouter`. Tracks per-key credit usage (charged cents vs
limit cents, the only window OpenRouter exposes). Useful for users who
proxy multiple model providers through OpenRouter.

1. Open <https://openrouter.ai> in your browser and **sign in**.
2. **Keys** → **Create Key**. Copy the value (starts with
   `sk-or-v1-`).
3. Run:
   ```bash
   custats add --provider openrouter --alias or --api-key "sk-or-v1-…"
   ```

The adapter calls `/api/v1/auth/key` (read-only) — it never invokes
paid inference. A 401 means the key was revoked or you mistyped it;
re-run `add` with the new value.

---

## 9. DeepSeek

Provider `deepseek`. Tracks remaining USDT credit (the only signal
DeepSeek's public API exposes). No percentage windows — the tray
shows the dollar balance in the popup's *Credits* row.

1. Open <https://platform.deepseek.com> in your browser and **sign in**.
2. **API Keys** → **Create new secret**. Copy the value (starts with
   `sk-`).
3. Run:
   ```bash
   custats add --provider deepseek --alias ds --api-key "sk-…"
   ```

The adapter calls `/user/balance` once per poll — read-only.

---

## 10. Mistral AI

Provider `mistral`. Tracks remaining USD credit on Mistral La Plateforme
(the API behind Le Chat, Mistral's API console, and the La Plateforme
partners). The adapter hits Mistral's balance endpoint once per poll and
surfaces the dollar value in the popup's *Credits* row. There are no
percentage windows — Mistral doesn't publish rolling time-window quotas
on the public API.

1. Open <https://console.mistral.ai> in your browser and **sign in**.
2. **API Keys** → **Create new key**. Copy the value (starts with a
   random string — no fixed prefix).
3. Run:
   ```bash
   custats add --provider mistral --alias mis --api-key "<value>"
   ```

The adapter calls `/v1/users/me/usage/balance` once per poll — read-only.
A 401 means the key was revoked; re-run `add` with the new value.

---

## 11. Kimi (Moonshot AI)

Provider `kimi`. Tracks remaining CNY credit on the Moonshot / Kimi
platform. The adapter hits Moonshot's billing-credit endpoint once per
poll and surfaces the converted dollar value in the popup's *Credits*
row. There are no percentage windows.

1. Open <https://platform.moonshot.cn> in your browser and **sign in**.
2. **API Keys** → **Create new key**. Copy the value. (If you've
   already run `kimi login` from the Kimi CLI, the same key works —
   paste the value it printed at sign-in.)
3. Run:
   ```bash
   custats add --provider kimi --alias kimi --api-key "<value>"
   ```

The adapter calls `/v1/dashboard/billing/credit` once per poll — read-only.
A 401 means the key was revoked; re-run `add` with the new value.

---

## 12. GitHub Copilot

Provider `copilot`. Tracks GitHub Copilot subscription quota (Business /
Pro / Enterprise / Individual plans). Auth uses GitHub's public
device-flow OAuth — the same RFC 8628 pattern GitHub's own `gh` CLI
uses for Copilot.

### 12a. GitHub Copilot — browser sign-in (recommended)

GitHub publishes a public OAuth device-flow endpoint at
`https://github.com/login/device/code` with a public client_id shared
across all integrations (`Iv23li5gtHhsd3iHGyqx`). `custats login
--provider copilot` walks through the standard device-code flow:

```bash
custats login --provider copilot --alias pro
# Visit https://github.com/login/device, enter the code, approve.
# custats writes the token bundle to ~/.config/custats/copilot.json
# (XDG config dir — not ~/.copilot/, because GitHub doesn't ship a
# CLI whose auth file we'd be aliasing) and adds the account.
```

After the device-code request returns, the CLI prints:

```
Enter code: WXYZ-9876
(expires in 14:59 — press Ctrl-C to cancel)
```

Open the URL in any browser, enter the code, approve the requested
scope (`copilot read:user`). custats writes `copilot.json` and inserts
the account row.

> GitHub's OAuth endpoint is **not** behind Cloudflare's bot-management
> — unlike OpenAI's `auth.openai.com`, a bare `httpx` POST goes through
> cleanly. The only header GitHub requires is `User-Agent`, which
> custats sends automatically.

### 12b. What the menu bar shows

Once polling starts, the Copilot account card in the tray popup shows
the consumed percentage from your most relevant quota bucket — by
default `monthly_ide_chat` (the IDE chat quota, the most user-visible
limit). The adapter falls back to `monthly_agent_chat`, then
`monthly_ide_completions` if the preferred bucket is missing from the
upstream response. Unlimited buckets surface as 0%.

The raw response (`copilot_plan`, `access_type_sku`, `chat_enabled`,
all three quota buckets) is preserved in the usage snapshot's `raw`
field for future dashboard / popup enhancements.

### 12c. Refresh

GitHub Copilot tokens expire in ~8 hours and are auto-refreshed on
401 — the adapter calls
`https://github.com/login/oauth/access_token` with `grant_type=refresh_token`
to rotate the bundle, persists the new `access_token` /
`refresh_token` to `copilot.json`, and retries the poll exactly once.

Force-refresh manually (alongside every other refreshable account):

```bash
custats refresh --all   # Codex + ChatGPT + GitHub Copilot
```

---

## Verifying it works

After adding one or more accounts:

```bash
custats list                       # show what custats sees
custats doctor                     # preflight + GTK checks
custats run                        # foreground; tray icon appears
```

Within ~60s (the default `refresh_interval_seconds`) the tray icon should
light up with one of the provider letters from `docs/design-tokens.md` §2.

If you see `! <error>` in the popup, run `custats doctor` and check
`journalctl --user -u custats.service -n 50` for the upstream HTTP status
code — most commonly this is a 401 (re-run the `add` flow for the affected
account).

---

## Refreshing credentials

Sessions rotate. Plan to re-extract credentials when:

- The provider UI makes you sign in again.
- You see a 401/403 in the custats logs.
- Your `add` command was run more than ~30 days ago (provider-dependent).

The cleanest workflow:

```bash
custats list                              # copy the failing account's id
custats remove --account-id <id>          # drop the stale entry
custats add --provider <name> --alias ... # add it back with a fresh value
```
