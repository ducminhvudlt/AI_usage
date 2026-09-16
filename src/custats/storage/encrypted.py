"""AES-GCM helpers for at-rest credential encryption.

We use the stdlib-friendly ``cryptography`` package and keep the
key format intentionally simple: 32 random bytes written with
mode ``0600``. The wire format for an encrypted blob is the
concatenation of the 12-byte GCM nonce and the ciphertext
(GCM appends its own 16-byte tag, so no extra bookkeeping is
required).

TODO(Phase 6): consider reintroducing ``keyring`` to back the on-disk
secret key with an OS-level keyring (Secret Service / Keychain).
For Phase 1 we keep the keyfile-only flow to minimize dependencies.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

KEY_SIZE_BYTES = 32
NONCE_SIZE_BYTES = 12
TAG_SIZE_BYTES = 16  # GCM standard tag length; included in ciphertext


def _validate_key(key: bytes) -> None:
    """Raise :class:`ValueError` if ``key`` is the wrong length for AES-256."""
    if not isinstance(key, (bytes, bytearray, memoryview)):
        raise TypeError(f"key must be bytes-like, got {type(key).__name__}")
    if len(key) != KEY_SIZE_BYTES:
        raise ValueError(
            f"AES-256-GCM key must be {KEY_SIZE_BYTES} bytes, got {len(key)}"
        )


def load_or_create_key(key_path: Path) -> bytes:
    """Return the AES-256 key at ``key_path``, creating it if missing.

    If the existing file is the wrong length, it is regenerated. The
    file is written atomically and chmod'd to ``0o600`` so that
    other users on the system can't read the secret material.
    """
    key_path = Path(key_path)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    if key_path.exists():
        existing = key_path.read_bytes()
        if len(existing) == KEY_SIZE_BYTES:
            # Tighten permissions on the existing file in case they were
            # relaxed (e.g. someone copied the keyfile with a wider umask).
            try:
                os.chmod(key_path, 0o600)
            except OSError:
                # Best-effort on filesystems that don't support chmod.
                pass
            return existing
        # Wrong size — regenerate.
        try:
            key_path.unlink()
        except OSError:
            pass

    new_key = secrets.token_bytes(KEY_SIZE_BYTES)
    tmp_path = key_path.with_suffix(key_path.suffix + ".tmp")
    tmp_path.write_bytes(new_key)
    os.chmod(tmp_path, 0o600)
    tmp_path.replace(key_path)
    try:
        os.chmod(key_path, 0o600)
    except OSError:
        # Best-effort on filesystems that don't support chmod.
        pass
    return new_key


def encrypt(key: bytes, plaintext: bytes, aad: bytes | None = None) -> bytes:
    """Encrypt ``plaintext`` with AES-256-GCM.

    Returns ``nonce(12) || ciphertext+tag``.
    """
    _validate_key(key)
    if not isinstance(plaintext, (bytes, bytearray, memoryview)):
        raise TypeError("plaintext must be bytes-like")
    aesgcm = AESGCM(bytes(key))
    nonce = secrets.token_bytes(NONCE_SIZE_BYTES)
    ciphertext = aesgcm.encrypt(nonce, bytes(plaintext), aad)
    return nonce + ciphertext


def decrypt(key: bytes, blob: bytes, aad: bytes | None = None) -> bytes:
    """Decrypt a blob produced by :func:`encrypt`.

    Raises :class:`cryptography.exceptions.InvalidTag` if the tag
    check fails (i.e. the data was tampered with or the wrong
    ``aad`` was supplied).
    """
    _validate_key(key)
    if not isinstance(blob, (bytes, bytearray, memoryview)):
        raise TypeError("blob must be bytes-like")
    if len(blob) < NONCE_SIZE_BYTES + TAG_SIZE_BYTES:
        raise ValueError("encrypted blob is too short to be valid")
    nonce = bytes(blob[:NONCE_SIZE_BYTES])
    ciphertext = bytes(blob[NONCE_SIZE_BYTES:])
    aesgcm = AESGCM(bytes(key))
    return aesgcm.decrypt(nonce, ciphertext, aad)


__all__ = [
    "KEY_SIZE_BYTES",
    "NONCE_SIZE_BYTES",
    "TAG_SIZE_BYTES",
    "decrypt",
    "encrypt",
    "load_or_create_key",
]
