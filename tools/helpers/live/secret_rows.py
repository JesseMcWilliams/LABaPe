"""secret_rows.py: every platform secret's health at a glance (read-only)."""
from oidc_login import LABAPE, login

c = login("labape-admin")
d = c.get(f"{LABAPE}/api/admin/secrets").json()
for p in d["policies"]:
    if p["section"] == "stack":
        print(f"{p['kind']:22} {p['check_status']:10} {p['status']:8} elev={p['elevated_ref'] or '-':18} {(p['check_error'] or '')[:90]}")
print("elevated:", d["elevated"])
