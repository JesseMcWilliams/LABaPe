"""Move real secrets between the stores and check what uses them still works (design §25, P2).

Usage: stores_move.py [environment name]
Needs both test stores set up in LABaPe (run stores.py first). It moves the
directory token built-in -> OpenBao -> Conjur -> built-in, running the
directory's group search after each move, and moves an environment's
credentials sheet (default: lab2) to OpenBao and back, comparing what the
Credentials view shows. It sets the default store for new environment
secrets to OpenBao, checks it's saved, and puts it back. Compares hashes,
never prints a secret.
"""
import hashlib
import sys

from oidc_login import LABAPE, login

DIR_TOKEN = "settings/directory_token"
env_name = sys.argv[1] if len(sys.argv) > 1 else "lab2"
admin = login("labape-admin")


def where(name):
    rows = admin.get(f"{LABAPE}/api/admin/stored-secrets").json()["secrets"]
    return next((r["store"] for r in rows if r["name"] == name), "missing")


def move(name, store):
    r = admin.post(f"{LABAPE}/api/admin/stored-secrets/move", json={"names": [name], "store": store}).json()
    res = r["results"][0]
    print(f"  move {name} -> {store}:", "ok" if res["ok"] else "FAILED", "-", res["message"])
    return res["ok"]


cat = admin.get(f"{LABAPE}/api/admin/stored-secrets").json()
by_store = {}
for row in cat["secrets"]:
    by_store[row["store"]] = by_store.get(row["store"], 0) + 1
print("catalog:", by_store, "available:", [s["name"] for s in cat["stores"]])
print("old 'passwords' secrets left:", [r["name"] for r in cat["secrets"] if r["name"].endswith("/passwords")])
linked = [r["name"] for r in cat["secrets"] if r["rotation"]]
print("linked to a rotation policy:", linked)

print("directory token:", where(DIR_TOKEN))
for store in ("openbao", "conjur", "builtin"):
    if move(DIR_TOKEN, store):
        t = admin.post(f"{LABAPE}/api/admin/settings/directory/test")
        print(f"  group search with the token in {where(DIR_TOKEN)}:", t.status_code, t.json().get("message", "")[:80])

envs = admin.get(f"{LABAPE}/api/environments").json()
envs = envs.get("environments", envs) if isinstance(envs, dict) else envs
env = next((e for e in envs if e["name"] == env_name), None)
if env is None:
    print(f"no environment {env_name}; skipping the credentials move")
else:
    name = f"environment/{env['id']}/credentials"

    def sheet():
        r = admin.get(f"{LABAPE}/api/environments/{env['id']}/credentials").json()
        return hashlib.sha256(r["credentials"].encode()).hexdigest()[:12], len(r["sections"])
    before = sheet()
    print(f"{env_name} credentials: {where(name)}, sheet {before[0]} ({before[1]} sections)")
    if move(name, "openbao"):
        print("  sheet read from OpenBao matches:", sheet() == before)
        move(name, "builtin")
        print("  sheet back in the built-in store matches:", sheet() == before)

for store in ("openbao", "builtin"):
    r = admin.put(f"{LABAPE}/api/admin/settings/secret_stores", json={"default_environment_store": store})
    print(f"default store for new environment secrets -> {store}:", r.status_code,
          r.json().get("default_environment_store"))
