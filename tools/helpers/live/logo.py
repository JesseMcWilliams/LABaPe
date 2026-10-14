"""The logo (design §44): upload a generated test PNG, check the public URL, then remove it.

Usage: logo.py roundtrip
Signs in as labape-admin; leaves no logo behind.
"""
import struct
import zlib

import httpx

from oidc_login import LABAPE, login


def png(w=32, h=32, rgb=(30, 120, 200)) -> bytes:
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    raw = b"".join(b"\x00" + bytes(rgb) * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


c = login("labape-admin")
data = png()
r = c.put(f"{LABAPE}/api/admin/settings/branding/logo", content=data, headers={"Content-Type": "image/png"})
print("upload:", r.status_code, r.json())
pub = httpx.get(f"{LABAPE}/api/branding/logo", verify=False)
print("public:", pub.status_code, pub.headers.get("content-type"), pub.content == data, pub.headers.get("content-security-policy"))
print("branding:", httpx.get(f"{LABAPE}/api/branding", verify=False).json())
print("remove:", c.delete(f"{LABAPE}/api/admin/settings/branding/logo").json())
