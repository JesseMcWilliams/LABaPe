"""CyberArk Conjur provider: a host identity signs in with its API key.

LABaPe name `a/b` is variable `<branch>/a/b` in a policy branch the host owns
(Reference_Secrets-Backend-Requirements.md §4). The first write declares the
variable by loading `- !variable` into that branch.
"""
from __future__ import annotations

from urllib.parse import quote

from pydantic import BaseModel, Field

from .base import HttpStore, StoreError, decode_name, encode_name

_ID = r"[A-Za-z0-9._-]+(/[A-Za-z0-9._-]+)*"
TOKEN_SECONDS = 8 * 60          # Conjur access tokens last 8 minutes
PAGE = 1000


class ConjurSettings(BaseModel):
    enabled: bool = False
    url: str = Field(default="", pattern=r"^(https://[^\s/]+/?)?$")
    account: str = Field(default="labape", pattern=r"^[A-Za-z0-9._-]+$")
    login: str = Field(default="host/labape/app", pattern=rf"^{_ID}$")
    authenticator: str = Field(default="authn", pattern=rf"^{_ID}$")
    branch: str = Field(default="labape/secrets", pattern=rf"^{_ID}$")
    verify_tls: bool = True
    ca_bundle: str = Field(default="", max_length=64000)


def _q(s: str) -> str:
    return quote(s, safe="")


class ConjurStore(HttpStore):
    name = "conjur"
    display_name = "CyberArk Conjur"
    settings_model = ConjurSettings
    credential_field = "api_key"
    credential_label = "Host API key"

    def _login(self, client):
        r = self._send(client, "POST", f"/{self.cfg.authenticator}/{_q(self.cfg.account)}/{_q(self.cfg.login)}/authenticate",
                       content=self._credential.encode(), headers={"Accept-Encoding": "base64"})
        if r.status_code in (401, 403):
            raise StoreError(f"{self.display_name} refused the login of {self.cfg.login}")
        if r.status_code != 200 or not r.text.strip():
            raise StoreError(f"{self.display_name} answered HTTP {r.status_code} to the login")
        return r.text.strip(), TOKEN_SECONDS

    def _auth_headers(self, token):
        return {"Authorization": f'Token token="{token}"'}

    def _id(self, name: str) -> str:
        return f"{self.cfg.branch}/{encode_name(name)}"

    def _secret_url(self, name: str) -> str:
        return f"/secrets/{_q(self.cfg.account)}/variable/{_q(self._id(name))}"

    def _policy(self, method: str, body: str, what: str) -> None:
        self._call(method, f"/policies/{_q(self.cfg.account)}/policy/{_q(self.cfg.branch)}", what,
                   ok=(200, 201), content=body.encode(), headers={"Content-Type": "application/x-yaml"})

    def get(self, name):
        r = self._call("GET", self._secret_url(name), name, missing_ok=True)
        return None if r is None else r.text

    def put(self, name, value):
        r = self._call("POST", self._secret_url(name), name, ok=(201, 204), missing_ok=True, content=value.encode())
        if r is None:                                  # not declared yet: declare it, then set it
            self._policy("POST", f'- !variable\n  id: "{encode_name(name)}"\n', name)
            self._call("POST", self._secret_url(name), name, ok=(201, 204), content=value.encode())

    def _exists(self, name: str) -> bool:
        return self._call("GET", f"/resources/{_q(self.cfg.account)}/variable/{_q(self._id(name))}", name,
                          missing_ok=True) is not None

    def delete(self, name):
        if self._exists(name):
            self._policy("PATCH", f'- !delete\n  record: !variable "{encode_name(name)}"\n', name)

    def list(self, prefix=""):
        head = f"{self.cfg.account}:variable:{self.cfg.branch}/"
        want = encode_name(prefix.strip("/")) if prefix.strip("/") else ""
        out, offset = [], 0
        while True:
            r = self._call("GET", f"/resources/{_q(self.cfg.account)}/variable", "the variable list",
                           params={"limit": PAGE, "offset": offset, "search": self.cfg.branch})
            rows = r.json()
            for row in rows:
                rid = row.get("id", "")
                if rid.startswith(head) and rid[len(head):].startswith(want):
                    out.append(decode_name(rid[len(head):]))
            if len(rows) < PAGE:
                return sorted(out)
            offset += PAGE

    def test_connection(self):
        try:
            r = self._call("GET", "/whoami", "its own identity")
            who = r.json().get("username", self.cfg.login)
            self._call("GET", f"/resources/{_q(self.cfg.account)}/policy/{_q(self.cfg.branch)}", "its policy branch")
        except (StoreError, ValueError) as exc:
            return False, str(exc) if isinstance(exc, StoreError) else f"{self.display_name} sent an odd answer"
        return True, f"Signed in as {who}; policy branch {self.cfg.branch} is visible"
