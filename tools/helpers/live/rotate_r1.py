"""Rotate the worker-run (phase R1) secrets end to end on the test Authentik.

Usage: rotate_r1.py
Adds the test Authentik's rotator token as the elevated account
"authentik-rotator", points the directory token's policy at it, then rotates
the directory token (and checks the old key now gets 403), the data key (and
checks the store's self-test) and the worker's SSH key.
Needs LABAPE_TEST_ROTATOR_TOKEN and LABAPE_TEST_DIRECTORY_TOKEN in the test
Authentik's .env. AK_URL overrides Authentik's address (default http://<LABAPE host>:9000).
"""
import os
import time
from urllib.parse import urlparse

import httpx

from oidc_login import ENV, LABAPE, login

env = dict(line.strip().split("=", 1) for line in open(ENV) if "=" in line and not line.startswith("#"))
ak_url = os.environ.get("AK_URL", f"http://{urlparse(LABAPE).hostname}:9000")
admin = login("labape-admin")


def run(policy_id):
    job = admin.post(f"{LABAPE}/api/admin/secrets/{policy_id}/rotate").json()["job_id"]
    while (j := admin.get(f"{LABAPE}/api/jobs/{job}").json())["state"] in ("queued", "running"):
        time.sleep(2)
    return j["state"]


pols = {p["kind"]: p for p in admin.get(f"{LABAPE}/api/admin/secrets").json()["policies"]}
print("policies:", len(pols), sorted(pols))
r = admin.post(f"{LABAPE}/api/admin/secrets/elevated",
               json={"name": "authentik-rotator", "value": env["LABAPE_TEST_ROTATOR_TOKEN"],
                     "description": "test Authentik: may set token keys"})
print("elevated account:", r.status_code)
d = pols["directory_token"]
body = {k: d[k] for k in ("interval_hours", "enabled", "managed_externally", "store", "params", "notes")}
print("set elevated_ref:", admin.put(f"{LABAPE}/api/admin/secrets/{d['id']}",
                                     json={**body, "elevated_ref": "authentik-rotator"}).status_code)
print("directory test before:", admin.post(f"{LABAPE}/api/admin/settings/directory/test").json().get("ok"))
print("rotate directory token:", run(d["id"]))
print("directory test after:", admin.post(f"{LABAPE}/api/admin/settings/directory/test").json().get("ok"))
old = httpx.get(f"{ak_url}/api/v3/core/groups/", headers={"Authorization": f"Bearer {env['LABAPE_TEST_DIRECTORY_TOKEN']}"})
print("old directory token now (expect 403):", old.status_code)
print("rotate data key:", run(pols["builtin_data_key"]["id"]))
h = admin.get(f"{LABAPE}/api/dashboard/admin").json()
print("store self-test:", [c["status"] + ": " + c["message"] for c in h["secrets"] if c["name"] == "builtin"])
print("rotate worker key:", run(pols["worker_ssh_key"]["id"]))
for k in ("directory_token", "builtin_data_key", "worker_ssh_key"):
    p = admin.get(f"{LABAPE}/api/admin/secrets/{pols[k]['id']}").json()
    print(f"  {k}: {p['status']} last {p['last_rotated_at']} next {p['next_due_at']}")
