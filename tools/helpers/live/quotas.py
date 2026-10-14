"""Quotas (design §41): list them, or a round trip that limits a test user and checks the refusal.

Usage: quotas.py [list] | roundtrip
`roundtrip` gives labape-dev a quota of 0 environments, checks /api/quotas/me and that creating an
environment as labape-dev is refused, then removes the quota.
"""
import sys

from oidc_login import LABAPE, login

admin = login("labape-admin")
if sys.argv[1:2] == ["roundtrip"]:
    q = admin.post(f"{LABAPE}/api/admin/quotas", json={"principal_type": "user", "principal": "labape-dev",
                                                       "max_environments": 0}).json()
    dev = login("labape-dev")
    me = dev.get(f"{LABAPE}/api/quotas/me").json()
    print("labape-dev roles/quota:", dev.get(f"{LABAPE}/api/auth/me").json().get("roles"), me)
    host = next(h["id"] for h in admin.get(f"{LABAPE}/api/hosts").json() if h["enabled"])
    r = dev.post(f"{LABAPE}/api/environments", json={"name": "quotatest", "kvm_host_id": host, "host_groups": [
        {"name": "web", "os": "debian_latest", "roles": ["linux_server"]}]})
    print("create as labape-dev:", r.status_code, r.text[:200])
    print("remove quota:", admin.delete(f"{LABAPE}/api/admin/quotas/{q['id']}").status_code)
for x in admin.get(f"{LABAPE}/api/admin/quotas").json():
    print(" ", x)
