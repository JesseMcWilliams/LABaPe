"""An environment's secrets on its page (V1a, design §49), through the API as labape-admin.

Usage: env_secrets.py <environment id> list
       env_secrets.py <environment id> roundtrip
`list` prints each secret's name, area, VM and place in the store. `roundtrip` also reveals each
one (which moves a secret still under its name from before the layout), then stores, replaces,
reveals and deletes a test secret shared and one for the first VM. Prints lengths, never values.
"""
import secrets
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
env_id, what = sys.argv[1], sys.argv[2]
base = f"{LABAPE}/api/environments/{env_id}/secrets"


def show():
    data = c.get(base).json()
    print("folder:", data["folder"], "vms:", data["vms"])
    for s in data["secrets"]:
        print(f"  {s['area']:8} {s['vm'] or '-':8} {s['name'][:40]:40} {s.get('login') or '-':26} {s['store']:8} {s['path']}")
    return data


data = show()
if what == "roundtrip":
    for s in data["secrets"]:
        r = c.post(f"{base}/reveal", json={"name": s["name"]})
        print("reveal", s["name"], r.status_code, "length", len(r.json().get("value", "")) if r.status_code == 200 else r.text[:120])
    vm = data["vms"][0]
    for body in ({"name": "v1a-test"}, {"name": "v1a-test", "vm": vm}):
        first, second = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
        r1 = c.put(base, json={**body, "value": first}).json()
        r2 = c.put(base, json={**body, "value": second}).json()
        got = c.post(f"{base}/reveal", json={"name": r1["name"]}).json()["value"]
        print("store", r1, "replace", r2, "reads back the replacement:", got == second)
    show()
    for name in ("user/v1a-test", f"uservm/{vm}/v1a-test"):
        print("delete", name, c.delete(base, params={"name": name}).status_code)
    print("left:", [s["name"] for s in c.get(base).json()["secrets"] if "v1a-test" in s["name"]])
