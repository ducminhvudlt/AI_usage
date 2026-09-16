"""custats storage layer.

Contains the SQLite-backed persistence and AES-GCM credential
encryption helpers. Both modules are stdlib-first; the only
external dependency is ``cryptography``.
"""

from __future__ import annotations
