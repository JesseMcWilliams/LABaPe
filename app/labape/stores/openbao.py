"""OpenBao / HashiCorp Vault provider: KV v2 with AppRole sign-in.

LABaPe name `a/b` is `<mount>/data/<prefix>/a/b`, value in the `value` field
(Reference_Secrets-Backend-Requirements.md §4). Vault speaks the same API.
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from .base import HttpStore, StoreError, decode_name, encode_name, url_path

_SEGMENT = r"[A-Za-z0-9._-]+"


class OpenBaoSettings(BaseModel):
    enabled: bool = False
    url: str = Field(default="", pattern=r"^(https://[^\s/]+/?)?$")
    mount: str = Field(default="labape", pattern=rf"^{_SEGMENT}(/{_SEGMENT})*$")
    path_prefix: str = Field(default="", pattern=rf"^({_SEGMENT}(/{_SEGMENT})*)?$")
    auth_mount: str = Field(default="approle", pattern=rf"^{_SEGMENT}(/{_SEGMENT})*$")
    role_id: str = Field(default="", max_length=200)
    namespace: str = Field(default="", pattern=rf"^({_SEGMENT}(/{_SEGMENT})*)?$")   # Vault Enterprise only
    verify_tls: bool = True
    ca_bundle: str = Field(default="", max_length=64000)


class OpenBaoStore(HttpStore):
    name = "openbao"
    display_name = "OpenBao / Vault"
    settings_model = OpenBaoSettings
    credential_field = "secret_id"
    credential_label = "AppRole secret ID"

    def _ns(self) -> dict[str, str]:
        return {"X-Vault-Namespace": self.cfg.namespace} if self.cfg.namespace else {}

    def _login(self, client):
        r = self._send(client, "POST", f"/v1/auth/{self.cfg.auth_mount}/login", headers=self._ns(),
                       json={"role_id": self.cfg.role_id, "secret_id": self._credential})
        if r.status_code in (400, 401, 403):
            raise StoreError(f"{self.display_name} refused the AppRole login")
        if r.status_code != 200:
            raise StoreError(f"{self.display_name} answered HTTP {r.status_code} to the AppRole login")
        try:
            auth = r.json()["auth"]
            return auth["client_token"], float(auth.get("lease_duration") or 300)
        except (ValueError, KeyError, TypeError) as exc:
            raise StoreError(f"{self.display_name} sent a login answer LABaPe doesn't understand") from exc

    def _auth_headers(self, token):
        return {"X-Vault-Token": token, **self._ns()}

    def _path(self, name: str) -> str:
        p = encode_name(name)
        return f"{self.cfg.path_prefix}/{p}" if self.cfg.path_prefix else p

    def get(self, name):
        r = self._call("GET", f"/v1/{self.cfg.mount}/data/{url_path(self._path(name))}", name, missing_ok=True)
        if r is None:
            return None
        try:
            data = (r.json().get("data") or {}).get("data") or {}
        except ValueError as exc:
            raise StoreError(f"{self.display_name} sent an answer LABaPe doesn't understand for {name}") from exc
        value = data.get("value")
        return value if isinstance(value, str) else None

    def put(self, name, value):
        self._call("POST", f"/v1/{self.cfg.mount}/data/{url_path(self._path(name))}", name,
                   json={"data": {"value": value}})

    def delete(self, name):
        # Metadata delete removes every version, so nothing is left to read back.
        self._call("DELETE", f"/v1/{self.cfg.mount}/metadata/{url_path(self._path(name))}", name, missing_ok=True)

    def list(self, prefix=""):
        base = self._path(prefix.rstrip("/")) if prefix.strip("/") else self.cfg.path_prefix
        out: list[str] = []
        todo = [base]
        while todo:
            folder = todo.pop()
            r = self._call("LIST", f"/v1/{self.cfg.mount}/metadata/{url_path(folder)}".rstrip("/") + "/",
                           decode_name(folder) or "the store's root", missing_ok=True)
            for key in ((r.json().get("data") or {}).get("keys") or []) if r is not None else []:
                full = f"{folder}/{key}" if folder else key
                if key.endswith("/"):
                    todo.append(full.rstrip("/"))
                else:
                    rel = full[len(self.cfg.path_prefix) + 1:] if self.cfg.path_prefix else full
                    out.append(decode_name(rel))
        return sorted(out)

    def test_connection(self):
        try:
            r = self._call("GET", "/v1/auth/token/lookup-self", "its own token")
            policies = ", ".join((r.json().get("data") or {}).get("policies") or []) or "none"
            root = url_path(self.cfg.path_prefix)
            self._call("LIST", f"/v1/{self.cfg.mount}/metadata/{root}".rstrip("/") + "/", "the store's root",
                       missing_ok=True)
        except StoreError as exc:
            return False, str(exc)
        return True, f"Signed in with AppRole; policies {policies}; KV v2 at {self.cfg.mount}/ is readable"
