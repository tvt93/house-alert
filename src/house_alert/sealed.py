"""Recipient addresses sealed for the mailing run.

The overview page lets anyone set up a daily mail without touching GitHub,
but the repository is public, so the address they type must not land in a
file readably. The page encrypts it in the browser with the public key
embedded in the page; the subscription file stores `enc:<base64>`; only the
Actions run, which holds the private key in the MAIL_KEY secret, can read it.

Wire format (all raw bytes, then base64):

    ephemeral public key (32) | nonce (24) | crypto_box ciphertext (16 + len)

That is plain NaCl `crypto_box` from a throw-away sender key to the site key,
which tweetnacl in the browser and PyNaCl here both implement unchanged.
"""

from __future__ import annotations

import base64

from nacl.exceptions import CryptoError
from nacl.public import Box, PrivateKey, PublicKey
from nacl.utils import random

PREFIX = "enc:"
_PK, _NONCE, _MAC = 32, 24, 16


class SealError(ValueError):
    pass


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    try:
        return base64.b64decode(text.strip(), validate=True)
    except (ValueError, TypeError) as exc:
        raise SealError("not valid base64") from exc


def keygen() -> tuple[str, str]:
    """(public key for the page, private key for the MAIL_KEY secret), base64."""
    sk = PrivateKey.generate()
    return _b64(bytes(sk.public_key)), _b64(bytes(sk))


def seal(text: str, public_key_b64: str) -> str:
    """What the browser does; here for tests and for sealing by hand."""
    pk = PublicKey(_unb64(public_key_b64))
    eph = PrivateKey.generate()
    nonce = random(_NONCE)
    ct = Box(eph, pk).encrypt(text.encode("utf-8"), nonce).ciphertext
    return PREFIX + _b64(bytes(eph.public_key) + nonce + ct)


def is_sealed(value: str) -> bool:
    return value.strip().startswith(PREFIX)


def open_sealed(token: str, private_key_b64: str) -> str:
    """The address inside an `enc:` token, or SealError."""
    token = token.strip()
    if not token.startswith(PREFIX):
        raise SealError("not an enc: token")
    raw = _unb64(token[len(PREFIX):])
    if len(raw) < _PK + _NONCE + _MAC + 1:
        raise SealError("token too short")
    eph_pk, nonce, ct = raw[:_PK], raw[_PK:_PK + _NONCE], raw[_PK + _NONCE:]
    try:
        sk = PrivateKey(_unb64(private_key_b64))
        plain = Box(sk, PublicKey(eph_pk)).decrypt(ct, nonce)
    except (CryptoError, ValueError) as exc:
        raise SealError("wrong key or damaged token") from exc
    return plain.decode("utf-8")
