"""The secret store interface (Claude_Docs/Reference_Secrets-Backend-Requirements.md §3).

A store holds values under LABaPe's slash-separated names
(`settings/smtp_password`, `environment/3/passwords`). External stores talk
HTTPS with fixed timeouts, check certificates against the platform root (or a
CA bundle from their settings), keep their session token in memory only, and
raise `StoreError` with a plain message that never carries a value, a token or
a response body.
"""
from __future__ import annotations

import re
import ssl
import time
from urllib.parse import quote, unquote

import httpx
from pydantic import BaseModel

from ..config import get_settings

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_OUTSIDE = re.compile(r"[^A-Za-z0-9._/-]")


class StoreError(RuntimeError):
    """A store operation failed; the message is safe to show and log."""


def encode_name(name: str) -> str:
    """LABaPe name -> store path: percent-encode anything outside
    [A-Za-z0-9._/-], and refuse names that could climb out of LABaPe's area."""
    parts = name.split("/")
    if not name or any(p in ("", ".", "..") for p in parts):
        raise StoreError(f"{name!r} isn't a valid secret name")
    return _OUTSIDE.sub(lambda m: "".join(f"%{b:02X}" for b in m.group().encode()), name)


def decode_name(path: str) -> str:
    return unquote(path)


def url_path(path: str) -> str:
    """A store path inside a URL: `%` from encode_name must reach the store as itself."""
    return quote(path, safe="/")


class SecretStore:
    """One secret store. Subclasses set the class attributes and the five operations."""

    name = ""
    display_name = ""
    settings_model: type[BaseModel] | None = None
    credential_field = ""          # write-only setting held in the built-in store
    credential_label = ""
    rotates_itself = False

    def get(self, name: str) -> str | None:
        raise NotImplementedError

    def put(self, name: str, value: str) -> None:
        raise NotImplementedError

    def delete(self, name: str) -> None:
        raise NotImplementedError

    def list(self, prefix: str = "") -> list[str]:
        raise NotImplementedError

    def test_connection(self) -> tuple[bool, str]:
        raise NotImplementedError


class HttpStore(SecretStore):
    """Shared plumbing for stores reached over HTTPS: TLS, timeouts, a session
    token renewed before it expires and fetched again after a 401 or 403."""

    transport: httpx.BaseTransport | None = None      # tests put an httpx.MockTransport here
    REFRESH_FRACTION = 0.8

    def __init__(self, cfg: BaseModel, credential: str):
        self.cfg = cfg
        self._credential = credential
        self._token: str | None = None
        self._token_until = 0.0

    # --- subclass hooks ---------------------------------------------------------
    def _login(self, client: httpx.Client) -> tuple[str, float]:
        """Sign in; return (session token, seconds it lasts)."""
        raise NotImplementedError

    def _auth_headers(self, token: str) -> dict[str, str]:
        raise NotImplementedError

    # --- plumbing ---------------------------------------------------------------
    def _verify(self) -> ssl.SSLContext | bool:
        if not self.cfg.verify_tls:
            return False
        if self.cfg.ca_bundle.strip():
            try:
                return ssl.create_default_context(cadata=self.cfg.ca_bundle)
            except ssl.SSLError as exc:
                raise StoreError(f"{self.display_name}: the CA bundle in its settings isn't valid PEM") from exc
        root = get_settings().ca_root
        if root.is_file():
            return ssl.create_default_context(cafile=str(root))
        return True                                     # no platform CA here: the system trust store

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.cfg.url.rstrip("/"), verify=self._verify(), timeout=TIMEOUT,
                            transport=self.transport)

    def _session(self, client: httpx.Client, fresh: bool = False) -> str:
        if fresh or not self._token or time.monotonic() >= self._token_until:
            self._token = None
            token, lasts = self._login(client)
            self._token, self._token_until = token, time.monotonic() + max(lasts * self.REFRESH_FRACTION, 5)
        return self._token

    def _send(self, client: httpx.Client, method: str, url: str, **kw) -> httpx.Response:
        try:
            return client.request(method, url, **kw)
        except httpx.TimeoutException as exc:
            raise StoreError(f"{self.display_name} at {self.cfg.url} didn't answer in time") from exc
        except httpx.HTTPError as exc:
            raise StoreError(f"Couldn't reach {self.display_name} at {self.cfg.url}: {_reason(exc)}") from exc

    def _call(self, method: str, url: str, what: str, *, ok=(200, 204), missing_ok=False, **kw) -> httpx.Response | None:
        """An authenticated request. `what` names the LABaPe secret for messages.
        Returns None for 404 when `missing_ok`."""
        base = kw.pop("headers", {})
        with self._client() as client:
            for attempt in (0, 1):                       # expired or revoked token: sign in again once
                token = self._session(client, fresh=attempt == 1)
                r = self._send(client, method, url, headers={**base, **self._auth_headers(token)}, **kw)
                if r.status_code not in (401, 403):
                    break
        if r.status_code == 404 and missing_ok:
            return None
        if r.status_code in (401, 403):
            raise StoreError(f"{self.display_name} denied access to {what}")
        if r.status_code not in ok:
            raise StoreError(f"{self.display_name} answered HTTP {r.status_code} for {what}")
        return r


def _reason(exc: Exception) -> str:
    text = str(exc) or type(exc).__name__
    return text[:200]
