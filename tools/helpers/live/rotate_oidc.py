"""Rotate the OIDC client secret end to end on the test Authentik.

Usage: rotate_oidc.py
Adds the test Authentik's rotator token as the elevated account
"authentik-rotator" (if it isn't there yet), points the OIDC client secret's
policy at it, rotates it, then checks: the old secret (LABAPE_TEST_CLIENT_SECRET
in Authentik's .env) is now refused by the token endpoint, and a fresh sign-in
still works. AK_URL overrides Authentik's address (default http://<LABAPE host>:9000). Never prints a secret.
"""
import os
import time
from urllib.parse import urlparse

import httpx

from oidc_login import ENV, LABAPE, login

env = dict(line.strip().split("=", 1) for line in open(ENV) if "=" in line and not line.startswith("#"))
ak_url = os.environ.get("AK_URL", f"http://{urlparse(LABAPE).hostname}:9000")
admin = login("labape-admin")
pols = {p["kind"]: p for p in admin.get(f"{LABAPE}/api/admin/secrets").json()["policies"]}
o = pols["oidc_client_secret"]
print("policy:", o["status"], "elevated:", o["elevated_ref"] or "(none)")
if "authentik-rotator" not in admin.get(f"{LABAPE}/api/admin/secrets").json()["elevated"]:
    r = admin.post(f"{LABAPE}/api/admin/secrets/elevated",
                   json={"name": "authentik-rotator", "value": env["LABAPE_TEST_ROTATOR_TOKEN"],
                         "description": "test Authentik: may set token keys and the client secret"})
    print("elevated account:", r.status_code)
body = {k: o[k] for k in ("interval_hours", "enabled", "managed_externally", "store", "params", "notes")}
print("set elevated_ref:", admin.put(f"{LABAPE}/api/admin/secrets/{o['id']}",
                                     json={**body, "elevated_ref": "authentik-rotator"}).status_code)
job = admin.post(f"{LABAPE}/api/admin/secrets/{o['id']}/rotate").json()["job_id"]
while (j := admin.get(f"{LABAPE}/api/jobs/{job}").json())["state"] in ("queued", "running"):
    time.sleep(2)
print("rotate:", j["state"])
p = admin.get(f"{LABAPE}/api/admin/secrets/{o['id']}").json()
print(f"  {p['status']} last {p['last_rotated_at']} next {p['next_due_at']} {p['last_error']}")
r = httpx.post(f"{ak_url}/application/o/token/", data={
    "grant_type": "authorization_code", "code": "labape-secret-check",
    "redirect_uri": f"{LABAPE}/api/auth/oidc/callback",
    "client_id": "labape", "client_secret": env["LABAPE_TEST_CLIENT_SECRET"]})
print("old secret now (expect invalid_client):", r.status_code, r.json().get("error"))
me = login("labape-dev").get(f"{LABAPE}/api/auth/me").json()
print("fresh sign-in with the new secret:", me.get("username"))
