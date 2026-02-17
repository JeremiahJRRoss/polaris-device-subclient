"""AES-256-GCM encrypted secrets file management.

File format::

    [8 bytes:  magic "POLSECRT"]
    [1 byte:   version = 0x01]
    [16 bytes: salt]
    [12 bytes: nonce]
    [N bytes:  ciphertext]
    [16 bytes: GCM auth tag]     ← appended by AESGCM automatically

Key derivation uses Scrypt (from ``cryptography``) when a passphrase is
provided.  When a key-file is used the raw 32 bytes are read directly.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

import orjson
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

MAGIC = b"POLSECRT"
VERSION = 0x01
SALT_LEN = 16
NONCE_LEN = 12


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """Derive a 32-byte key from *passphrase* using Scrypt."""
    kdf = Scrypt(salt=salt, length=32, n=2**14, r=8, p=1)
    return kdf.derive(passphrase.encode("utf-8"))


def _load_key(key_file: str | Path, passphrase: Optional[str] = None) -> bytes:
    """Return a 32-byte encryption key.

    If *key_file* exists, read it directly (must be exactly 32 bytes).
    Otherwise fall back to *passphrase* via Scrypt (requires a salt).
    """
    kf = Path(key_file)
    if kf.exists():
        key = kf.read_bytes()
        if len(key) != 32:
            raise ValueError(f"Key file must be exactly 32 bytes, got {len(key)}")
        return key
    if passphrase:
        raise ValueError("Passphrase-based key derivation requires a salt from the file header")
    raise FileNotFoundError(f"Key file not found: {key_file}")


def init_secrets(output: str | Path, key_file: str | Path) -> None:
    """Create an empty encrypted secrets file and (if needed) a new key file.

    Parameters
    ----------
    output:
        Path to write the ``.secrets.enc`` file.
    key_file:
        Path to the 32-byte master key.  Created if it does not exist.
    """
    kf = Path(key_file)
    if not kf.exists():
        kf.write_bytes(os.urandom(32))
        os.chmod(kf, 0o600)

    key = kf.read_bytes()
    _encrypt_store(Path(output), key, {})


def set_secret(
    secrets_file: str | Path,
    key_file: str | Path,
    name: str,
    value: str,
) -> None:
    """Add or update a single secret in the encrypted store."""
    key = _load_key(key_file)
    store = _decrypt_store(Path(secrets_file), key)
    store[name] = value
    _encrypt_store(Path(secrets_file), key, store)


def list_secrets(secrets_file: str | Path, key_file: str | Path) -> list[str]:
    """Return the names (not values) of all stored secrets."""
    key = _load_key(key_file)
    store = _decrypt_store(Path(secrets_file), key)
    return sorted(store.keys())


def load_secrets(secrets_file: str | Path, key_file: str | Path) -> dict[str, str]:
    """Decrypt and return the full secrets dict."""
    key = _load_key(key_file)
    return _decrypt_store(Path(secrets_file), key)


def rekey(
    secrets_file: str | Path,
    old_key_file: str | Path,
    new_key_file: str | Path,
) -> None:
    """Re-encrypt the secrets store with a new key."""
    old_key = _load_key(old_key_file)
    store = _decrypt_store(Path(secrets_file), old_key)

    nkf = Path(new_key_file)
    if not nkf.exists():
        nkf.write_bytes(os.urandom(32))
        os.chmod(nkf, 0o600)

    new_key = nkf.read_bytes()
    _encrypt_store(Path(secrets_file), new_key, store)


# ── internal helpers ────────────────────────────────────────────────

def _encrypt_store(path: Path, key: bytes, store: dict[str, str]) -> None:
    """Serialize *store* to JSON, encrypt, and write to *path*."""
    plaintext = orjson.dumps(store)
    salt = os.urandom(SALT_LEN)
    nonce = os.urandom(NONCE_LEN)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext, salt)  # includes 16-byte tag

    with open(path, "wb") as fh:
        fh.write(MAGIC)
        fh.write(bytes([VERSION]))
        fh.write(salt)
        fh.write(nonce)
        fh.write(ciphertext)

    os.chmod(path, 0o600)


def _decrypt_store(path: Path, key: bytes) -> dict[str, str]:
    """Read and decrypt the secrets file, returning the JSON dict."""
    data = path.read_bytes()

    if data[:8] != MAGIC:
        raise ValueError("Invalid secrets file (bad magic)")
    if data[8] != VERSION:
        raise ValueError(f"Unsupported secrets file version: {data[8]}")

    salt = data[9:9 + SALT_LEN]
    nonce = data[9 + SALT_LEN: 9 + SALT_LEN + NONCE_LEN]
    ciphertext = data[9 + SALT_LEN + NONCE_LEN:]

    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, salt)
    return orjson.loads(plaintext)
