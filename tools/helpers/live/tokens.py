"""API tokens (design §37): list them, or a round trip with a throwaway token.

Usage: tokens.py [list] | roundtrip
`roundtrip` makes a 1-day personal token as labape-admin, calls the API with it (no session),
makes one write (audited), revokes it and checks it's refused. The secret is never printed.
"""
import sys

import httpx

from oidc_login import LABAPE, login

c = login("labape-admin")
if sys.argv[1:2] == ["roundtrip"]:
    t = c.post(f"{LABAPE}/api/tokens", json={"name": "roundtrip-test", "days": 1}).json()
    h = {"Authorization": f"Bearer {t['secret']}"}
    raw = httpx.Client(verify=False, timeout=30)
    print("hosts with the token:", raw.get(f"{LABAPE}/api/hosts", headers=h).status_code)
    print("admin settings with it:", raw.get(f"{LABAPE}/api/admin/settings", headers=h).status_code)
    print("a token can't make tokens:", raw.post(f"{LABAPE}/api/tokens", json={"name": "x"}, headers=h).status_code)
    print("revoke:", c.delete(f"{LABAPE}/api/tokens/{t['id']}").json()["state"])
    print("after revoking:", raw.get(f"{LABAPE}/api/hosts", headers=h).status_code)
for x in c.get(f"{LABAPE}/api/tokens?all=true").json()["tokens"]:
    print(f"  #{x['id']} {x['name']} {x['kind']} {x['owner']} {x['roles']} {x['state']} last_used={str(x['last_used_at'])[:19]}")
