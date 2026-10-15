"""People signing in to OpenBao themselves (V1b, design §49.2), as a test user, without a browser.

Usage: openbao_people.py sync
       openbao_people.py try <user> <environment name> [write]
`sync` runs LABaPe's sync now (as labape-admin) and prints what changed. `try` signs <user> in to
OpenBao with OIDC (the `bao login -method=oidc` flow, through the test Authentik), prints the token's
policies, then tries reads (the environment's accounts/, the platform's engine vault password) and,
with `write`, writes and deletes user/v1b-test and tries a write to accounts/. Prints HTTP statuses,
never a value or a token.
"""
import re
import secrets
import sys
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

from oidc_login import LABAPE, login, password, post

admin = login("labape-admin")
if sys.argv[1] == "sync":
    r = admin.post(f"{LABAPE}/api/admin/openbao-people/sync")
    print("sync", r.status_code)
    for line in r.json().get("changes", [r.text[:300]]):
        print("  ", line)
    raise SystemExit
user, env_name = sys.argv[2], sys.argv[3]
env = next(e for e in admin.get(f"{LABAPE}/api/environments").json() if e["name"] == env_name)
info = admin.get(f"{LABAPE}/api/environments/{env['id']}/secrets").json()["openbao"]
if not info:
    raise SystemExit("this environment has no OpenBao access (people access off, or its secrets aren't in OpenBao)")
bao = info["url"].removesuffix("/ui/")
mount = info["mount"]
c = httpx.Client(verify=False, follow_redirects=False, timeout=30)
redirect = "http://localhost:8250/oidc/callback"
r = c.post(f"{bao}/v1/auth/oidc/oidc/auth_url", json={"role": "people", "redirect_uri": redirect})
url = r.json()["data"]["auth_url"]
# Through Authentik's flow executor, as oidc_login does, until it sends us to the redirect.
for _ in range(10):
    r = c.get(url)
    if r.status_code in (301, 302, 303, 307):
        url = urljoin(url, r.headers["location"])
        continue
    break
p = urlparse(url)
m = re.match(r"/if/flow/([^/]+)/", p.path)
if m:
    base = f"{p.scheme}://{p.netloc}"
    ex = f"{base}/api/v3/flows/executor/{m.group(1)}/?" + urlencode({"query": urlencode({"next": parse_qs(p.query).get('next', [''])[0]})})
    ch = c.get(ex).json()
    for _ in range(6):
        comp = ch.get("component")
        if comp == "ak-stage-identification":
            ch = post(c, ex, {"component": comp, "uid_field": user})
        elif comp == "ak-stage-password":
            ch = post(c, ex, {"component": comp, "password": password()})
        elif comp == "xak-flow-redirect":
            url = urljoin(base, ch["to"])
            break
        else:
            raise SystemExit(f"unexpected stage {comp}")
for _ in range(10):
    if url.startswith(redirect):
        break
    r = c.get(url)
    if r.status_code in (301, 302, 303, 307):
        url = urljoin(url, r.headers["location"])
        continue
    raise SystemExit(f"stuck at {urlparse(url).path} {r.status_code}")
q = parse_qs(urlparse(url).query)
r = c.get(f"{bao}/v1/auth/oidc/oidc/callback", params={"code": q["code"][0], "state": q["state"][0]})
auth = r.json().get("auth") or {}
print("oidc sign-in", r.status_code, "policies", auth.get("policies"), "identity", auth.get("identity_policies"))
if not auth:
    raise SystemExit(f"OpenBao refused the sign-in: {r.json().get('errors')}")
h = {"X-Vault-Token": auth["client_token"]}


def st(method, path, **kw):
    return c.request(method, f"{bao}/v1/{mount}/{path}", headers=h, **kw).status_code


print("read accounts/windows_admin:", st("GET", f"data/env/{env_name}/accounts/windows_admin"))
print("list the folder:", st("LIST", f"metadata/env/{env_name}"))
print("read the platform's engine vault password:", st("GET", "data/platform/engine/vault_password"))
if len(sys.argv) > 4 and sys.argv[4] == "write":
    print("write user/v1b-test:", st("POST", f"data/env/{env_name}/user/v1b-test", json={"data": {"value": secrets.token_urlsafe(18)}}))
    print("write accounts/v1b-test:", st("POST", f"data/env/{env_name}/accounts/v1b-test", json={"data": {"value": "x"}}))
c.post(f"{bao}/v1/auth/token/revoke-self", headers=h)
