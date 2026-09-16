"""GTK-based system tray UI for custats."""
from __future__ import annotations

from .tray import TrayIcon, TrayUnavailable
from .popup import PopupMenu

__all__ = ["TrayIcon", "TrayUnavailable", "PopupMenu"]
