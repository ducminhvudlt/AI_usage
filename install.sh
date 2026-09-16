#!/usr/bin/env bash
# Install custats on Ubuntu 22.04 (or any Debian-based distro with apt).
# Idempotent. Safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "==> custats installer"

# 1. System deps via apt
if ! command -v apt-get >/dev/null 2>&1; then
  echo "error: apt-get not found — this installer is Ubuntu/Debian-only." >&2
  echo "       Install python3, libgtk-3-1, gir1.2-gtk-3.0," >&2
  echo "       gir1.2-appindicator3-0.1, and libnotify-bin manually," >&2
  echo "       then re-run with system dependencies satisfied." >&2
  exit 1
fi

echo "==> Installing system dependencies (sudo required)"
if ! sudo -n true 2>/dev/null; then
  echo "    (you may be prompted for your password)"
fi
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    libgtk-3-0 gir1.2-gtk-3.0 gir1.2-appindicator3-0.1 \
    libnotify-bin

# 2. User-local venv + install
VENV="$HOME/.local/share/custats/venv"
echo "==> Creating venv at $VENV"
mkdir -p "$(dirname "$VENV")"
if [[ ! -d "$VENV" ]]; then
  python3 -m venv "$VENV"
else
  echo "    (venv already exists; reusing)"
fi

echo "==> Installing custats + deps into venv"
"$VENV/bin/pip" install --upgrade pip
"$VENV/bin/pip" install "$REPO_ROOT"

# 3. Symlink the binary into ~/.local/bin
mkdir -p "$HOME/.local/bin"
ln -sf "$VENV/bin/custats" "$HOME/.local/bin/custats"

# 4. Install the .desktop autostart
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart"
mkdir -p "$AUTOSTART"
cp "$REPO_ROOT/packaging/custats.desktop" "$AUTOSTART/custats.desktop"
# Ensure ~/.local/bin is on PATH (login shells only — print a hint).
if ! echo ":$PATH:" | grep -q ":$HOME/.local/bin:"; then
  echo "warning: $HOME/.local/bin is not on your PATH."
  echo "    Add it to your shell rc:"
  echo "        export PATH=\"\$HOME/.local/bin:\$PATH\""
fi

# 5. Install + enable the systemd user service (best effort).
if command -v systemctl >/dev/null 2>&1; then
  SYSTEMD_USER_DIR="$HOME/.config/systemd/user"
  mkdir -p "$SYSTEMD_USER_DIR"
  cp "$REPO_ROOT/packaging/custats.service" "$SYSTEMD_USER_DIR/custats.service"
  systemctl --user daemon-reload || true
  if systemctl --user enable custats.service 2>/dev/null; then
    echo "==> systemd user service installed and enabled."
    echo "    Start it now with: systemctl --user start custats.service"
    echo "    Or log out / log in for autostart to pick it up."
  else
    echo "==> Service file copied to $SYSTEMD_USER_DIR/custats.service,"
    echo "    but 'systemctl --user enable' failed (no active user session?)."
    echo "    The .desktop autostart entry will still launch custats on next login."
  fi
else
  echo "==> systemctl not available — skipping systemd service install."
  echo "    The .desktop autostart entry will pick up custats on next login."
fi

echo "==> Done. Try:"
echo "    custats add --provider claude --alias mywork --session-key <key>"
echo "    custats run"
