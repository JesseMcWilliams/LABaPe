"""Move an environment's secrets to another store and check they read the same (V1a, design §49).

Usage: env_secrets_move.py <environment id> <openbao|builtin>
Moves every secret of the environment (Admin → Secrets' Move), then reveals each again and compares
hashes with before; prints each one's place in the store. Never prints a value.
"""
import hashlib
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
env_id, target = sys.argv[1], sys.argv[2]
base = f"{LABAPE}/api/environments/{env_id}/secrets"


def digests():
    out = {}
    for s in c.get(base).json()["secrets"]:
        v = c.post(f"{base}/reveal", json={"name": s["name"]}).json().get("value", "")
        out[s["name"]] = hashlib.sha256(v.encode()).hexdigest()[:12]
    return out


before = digests()
names = [f"environment/{env_id}/{n}" for n in before]
r = c.post(f"{LABAPE}/api/admin/stored-secrets/move", json={"names": names, "store": target})
print("move", r.status_code, r.text[:600])
after = digests()
print("same values after the move:", before == after, f"({len(after)} secrets)")
for s in c.get(base).json()["secrets"]:
    print(f"  {s['name'][:40]:40} {s['store']:8} {s['path']}")
