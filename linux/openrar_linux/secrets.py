"""Saved-password protection on Linux.

A random 256-bit key lives in the desktop keyring (GNOME Keyring / KWallet / KeePassXC through the Secret
Service API, via the `keyring` package); passwords in settings.json are AES-256-GCM encrypted with it. When no
keyring is running (servers, minimal desktops) passwords fall back to plain obfuscation, and the Password
organizer says so.
"""
from __future__ import annotations

import base64
import os

SERVICE = "OpenRAR"
ACCOUNT = "password-organizer-key"
PREFIX = "kr:"


def _keyring():
    try:
        import keyring
        from keyring.backends import fail
    except ImportError:
        return None
    try:
        kr = keyring.get_keyring()
    except Exception:
        return None
    if isinstance(kr, fail.Keyring):
        return None
    if hasattr(kr, "backends") and not kr.backends:      # ChainerBackend with nothing usable
        return None
    return kr


def available() -> bool:
    return _keyring() is not None


def _key(create: bool) -> bytes | None:
    kr = _keyring()
    if kr is None:
        return None
    stored = kr.get_password(SERVICE, ACCOUNT)
    if not stored and create:
        stored = base64.b64encode(os.urandom(32)).decode()
        kr.set_password(SERVICE, ACCOUNT, stored)
    return base64.b64decode(stored) if stored else None


def protect(text: str) -> str | None:
    """Encrypted blob, or None when no keyring is available."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    try:
        key = _key(True)
    except Exception:
        return None
    if key is None:
        return None
    nonce = os.urandom(12)
    return PREFIX + base64.b64encode(nonce + AESGCM(key).encrypt(nonce, text.encode("utf-8"), None)).decode()


def unprotect(blob: str) -> str:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = _key(False)
    if key is None:
        raise ValueError("The desktop keyring holding the key for saved passwords is not available")
    raw = base64.b64decode(blob[len(PREFIX):])
    return AESGCM(key).decrypt(raw[:12], raw[12:], None).decode("utf-8")
