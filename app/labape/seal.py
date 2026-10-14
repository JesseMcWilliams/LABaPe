"""Sealed stack backups (design §32, batch B1): a stream encrypted with the backup key.

The full-stack backup is a tar stream that the host's agent pipes through `labape seal`, so the
host needs nothing but the container runtime. The format:

    MAGIC | salt (16) | nonce prefix (8) | chunks
    chunk: length (4, big-endian) | AES-256-GCM(chunk of at most CHUNK bytes)

The archive's key is HKDF-SHA256(backup key, salt). Each chunk's nonce is the prefix plus its
number, and its associated data says whether it's the last one, so a reordered, cut-short or
extended archive fails to open instead of restoring part of a stack.

The backup key is 32 random bytes, written as base32 in groups (`new_key`), and kept apart from
the archives (on the stack host for the agent, and wherever the admin records it): an archive is
useless without it, and the key isn't in any archive.
"""
from __future__ import annotations

import base64
import os
import re
import struct
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"LABAPESEAL1\n"
CHUNK = 1 << 20
INFO = b"labape stack backup v1"


class SealError(Exception):
    pass


def new_key() -> str:
    """A new backup key: 32 random bytes as base32, in groups of four for writing down."""
    s = base64.b32encode(os.urandom(32)).decode().rstrip("=")
    return "-".join(s[i:i + 4] for i in range(0, len(s), 4))


def parse_key(text: str) -> bytes:
    s = re.sub(r"[^A-Za-z2-7]", "", text).upper()
    try:
        raw = base64.b32decode(s + "=" * (-len(s) % 8))
    except ValueError as exc:
        raise SealError("that isn't a backup key") from exc
    if len(raw) != 32:
        raise SealError("that isn't a backup key (wrong length)")
    return raw


def _aead(key: bytes, salt: bytes) -> AESGCM:
    return AESGCM(HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=INFO).derive(key))


def _read(src: BinaryIO, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        b = src.read(n - len(buf))
        if not b:
            break
        buf += b
    return buf


def seal(src: BinaryIO, dst: BinaryIO, key: bytes) -> int:
    """Encrypt src into dst; returns the plaintext length."""
    salt, prefix = os.urandom(16), os.urandom(8)
    aead = _aead(key, salt)
    dst.write(MAGIC + salt + prefix)
    total, n = 0, 0
    cur = _read(src, CHUNK)
    while True:
        nxt = _read(src, CHUNK) if len(cur) == CHUNK else b""
        last = not nxt
        ct = aead.encrypt(prefix + struct.pack(">I", n), cur, b"\x01" if last else b"\x00")
        dst.write(struct.pack(">I", len(ct)) + ct)
        total += len(cur)
        n += 1
        if last:
            return total
        cur = nxt


def unseal(src: BinaryIO, dst: BinaryIO, key: bytes) -> int:
    """Decrypt src into dst; raises SealError on a wrong key or a damaged or truncated archive."""
    head = _read(src, len(MAGIC) + 24)
    if len(head) < len(MAGIC) + 24 or not head.startswith(MAGIC):
        raise SealError("not a sealed LABaPe backup")
    salt, prefix = head[len(MAGIC):len(MAGIC) + 16], head[len(MAGIC) + 16:]
    aead = _aead(key, salt)
    total, n = 0, 0
    while True:
        ln = _read(src, 4)
        if len(ln) < 4:
            raise SealError("the archive is cut short")
        ct = _read(src, struct.unpack(">I", ln)[0])
        nonce = prefix + struct.pack(">I", n)
        try:
            pt, last = aead.decrypt(nonce, ct, b"\x00"), False
        except InvalidTag:
            try:
                pt, last = aead.decrypt(nonce, ct, b"\x01"), True
            except InvalidTag as exc:
                raise SealError("wrong backup key, or the archive is damaged" if n == 0
                                else f"the archive is damaged (chunk {n})") from exc
        dst.write(pt)
        total += len(pt)
        n += 1
        if last:
            if src.read(1):
                raise SealError("unexpected data after the end of the archive")
            return total
