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
    role_name: str = Field(default="labape", pattern=rf"^{_SEGMENT}$")         # for rotating its secret ID
    role_id: str = Field(default="", max_length=200)
    namespace: str = Field(default="", pattern=rf"^({_SEGMENT}(/{_SEGMENT})*)?$")   # Vault Enterprise only
    verify_tls: bool = True
    ca_bundle: str = Field(default="", max_length=64000)
    # People's own sign-in (design §49.2, V1b): the address their browsers and `bao` use, and the
    # OIDC auth mount stores.sh sets up. Empty: people reach their secrets through LABaPe only.
    people_url: str = Field(default="", pattern=r"^(https://[^\s/]+/?)?$")
    people_mount: str = Field(default="oidc", pattern=rf"^{_SEGMENT}$")


class OpenBaoStore(HttpStore):
    name = "openbao"
    display_name = "OpenBao / Vault"
    settings_model = OpenBaoSettings
    credential_field = "secret_id"
    credential_label = "AppRole secret ID"

    def _ns(self) -> dict[str, str]:
        return {"X-Vault-Namespace": self.cfg.namespace} if self.cfg.namespace else {}

    def _login(self, client, secret_id: str | None = None):
        r = self._send(client, "POST", f"/v1/auth/{self.cfg.auth_mount}/login", headers=self._ns(),
                       json={"role_id": self.cfg.role_id, "secret_id": secret_id or self._credential})
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

    def api(self, method: str, path: str, body: dict | None = None, *, missing_ok: bool = False) -> dict:
        """Any OpenBao API call LABaPe's policy allows (design §49.2): the JSON answer, {} when there's
        none (204, or 404 with `missing_ok`)."""
        kw = {"json": body} if body is not None else {}
        r = self._call(method, f"/v1/{url_path(path)}", path, missing_ok=missing_ok, **kw)
        if r is None or r.status_code == 204 or not r.content:
            return {}
        try:
            return r.json()
        except ValueError as exc:
            raise StoreError(f"{self.display_name} sent an answer LABaPe doesn't understand for {path}") from exc

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

    # --- rotating its own secret ID (design §25, P3) ------------------------------
    def _role_call(self, op: str, token: str | None, body: dict | None = None) -> dict:
        """POST to the AppRole role's `secret-id` endpoints, with LABaPe's own
        session or an elevated token."""
        url = f"/v1/auth/{self.cfg.auth_mount}/role/{self.cfg.role_name}/{op}"
        with self._client() as client:
            headers = self._auth_headers(token or self._session(client))
            r = self._send(client, "POST", url, headers=headers, json=body or {})
        if r.status_code in (401, 403):
            raise StoreError(f"{self.display_name} doesn't let {'the elevated token' if token else 'LABaPe'} "
                             f"change role {self.cfg.role_name}'s secret IDs")
        if r.status_code not in (200, 204):
            raise StoreError(f"{self.display_name} answered HTTP {r.status_code} to {op}")
        return r.json() if r.status_code == 200 and r.content else {}

    def issue_secret_id(self, token: str | None = None) -> str:
        sid = (self._role_call("secret-id", token).get("data") or {}).get("secret_id")
        if not sid:
            raise StoreError(f"{self.display_name} didn't return a new secret ID")
        return sid

    def check_login(self, secret_id: str) -> bool:
        with self._client() as client:
            try:
                self._login(client, secret_id)
                return True
            except StoreError:
                return False

    def destroy_secret_id(self, secret_id: str, token: str | None = None) -> None:
        self._role_call("secret-id/destroy", token, {"secret_id": secret_id})
