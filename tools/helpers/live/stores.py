"""Check the secret stores LABaPe is set up with (design §25, §26).

Usage: stores.py
Runs each enabled store's Test connection through the API and prints the
admin dashboard's store checks. The stores are the ones in the stack
(container/stores/), whose logins LABaPe owns and rotates, so this script
never sees a login. Never prints a secret.
"""
from oidc_login import LABAPE, login

admin = login("labape-admin")
settings = admin.get(f"{LABAPE}/api/admin/settings").json()
for name in ("openbao", "conjur"):
    s = settings.get(f"store_{name}", {})
    if not s.get("enabled"):
        print(f"{name}: not enabled in Settings → Secret stores")
        continue
    t = admin.post(f"{LABAPE}/api/admin/settings/stores/{name}/test").json()
    print(f"test {name} ({s.get('url')}):", "ok" if t["ok"] else "FAILED", "-", t["message"])
checks = {c["name"]: c for c in admin.get(f"{LABAPE}/api/dashboard/admin").json()["secrets"]}
for name in ("store.openbao", "store.conjur"):
    print(f"dashboard {name}:", checks.get(name, {}).get("status", "missing"))
