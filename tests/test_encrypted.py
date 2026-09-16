"""Tests for custats.storage.encrypted."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from cryptography.exceptions import InvalidTag

from custats.storage.encrypted import (
    KEY_SIZE_BYTES,
    decrypt,
    encrypt,
    load_or_create_key,
)


@pytest.fixture
def key_path(tmp_path: Path) -> Path:
    return tmp_path / "secret.key"


class TestLoadOrCreateKey:
    def test_creates_when_missing(self, key_path: Path) -> None:
        assert not key_path.exists()
        key = load_or_create_key(key_path)
        assert key_path.exists()
        assert len(key) == KEY_SIZE_BYTES
        assert isinstance(key, bytes)

    def test_reuses_existing_key(self, key_path: Path) -> None:
        first = load_or_create_key(key_path)
        second = load_or_create_key(key_path)
        assert first == second

    def test_file_permissions_are_0600(self, key_path: Path) -> None:
        load_or_create_key(key_path)
        mode = stat.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600

    def test_regenerates_when_wrong_size(self, key_path: Path) -> None:
        key_path.write_bytes(b"too-short")
        key = load_or_create_key(key_path)
        assert len(key) == KEY_SIZE_BYTES

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "a" / "b" / "secret.key"
        load_or_create_key(nested)
        assert nested.is_file()

    def test_tightens_perms_on_existing_keyfile(self, key_path: Path) -> None:
        # Pre-create a keyfile with overly permissive mode.
        key_path.write_bytes(b"\x00" * KEY_SIZE_BYTES)
        os.chmod(key_path, 0o644)
        load_or_create_key(key_path)
        mode = stat.S_IMODE(key_path.stat().st_mode)
        assert mode == 0o600


class TestEncryptDecrypt:
    def test_round_trip(self) -> None:
        key = b"\x00" * KEY_SIZE_BYTES
        plaintext = b"hello, world"
        blob = encrypt(key, plaintext)
        assert decrypt(key, blob) == plaintext

    def test_ciphertext_differs_from_plaintext(self) -> None:
        key = b"\x00" * KEY_SIZE_BYTES
        plaintext = b"super secret value" * 16
        blob = encrypt(key, plaintext)
        # The blob is nonce(12) + ciphertext + tag(16). At minimum it
        # is longer than the plaintext and contains encrypted bytes.
        assert blob != plaintext
        assert len(blob) > len(plaintext)

    def test_different_nonces_each_call(self) -> None:
        key = b"\x00" * KEY_SIZE_BYTES
        a = encrypt(key, b"same plaintext")
        b = encrypt(key, b"same plaintext")
        assert a != b  # randomized nonce → different ciphertext

    def test_with_aad(self) -> None:
        key = b"\x00" * KEY_SIZE_BYTES
        plaintext = b"the eagle has landed"
        blob = encrypt(key, plaintext, aad=b"context-1")
        assert decrypt(key, blob, aad=b"context-1") == plaintext
        with pytest.raises(InvalidTag):
            decrypt(key, blob, aad=b"context-2")

    def test_tampered_ciphertext_raises(self) -> None:
        key = b"\x00" * KEY_SIZE_BYTES
        blob = bytearray(encrypt(key, b"hello"))
        # Flip one byte deep in the ciphertext region.
        blob[-5] ^= 0xFF
        with pytest.raises(InvalidTag):
            decrypt(key, bytes(blob))

    def test_wrong_key_raises(self) -> None:
        key = b"\x00" * KEY_SIZE_BYTES
        other = b"\x01" * KEY_SIZE_BYTES
        blob = encrypt(key, b"secret")
        with pytest.raises(InvalidTag):
            decrypt(other, blob)

    def test_too_short_blob_raises(self) -> None:
        key = b"\x00" * KEY_SIZE_BYTES
        with pytest.raises(ValueError):
            decrypt(key, b"\x00\x01\x02")

    @pytest.mark.parametrize(
        "key",
        [b"", b"\x00" * 16, b"\x00" * 64],
        ids=["empty", "16-bytes", "64-bytes"],
    )
    def test_bad_key_lengths_rejected(self, key: bytes) -> None:
        with pytest.raises(ValueError):
            encrypt(key, b"hi")
        with pytest.raises(ValueError):
            decrypt(key, b"\x00" * 32)
