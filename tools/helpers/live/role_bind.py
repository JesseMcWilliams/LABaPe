"""Group role bindings through the API as labape-admin: `list`, or `add GROUP ROLE` (skipped when it exists)."""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
base = f"{LABAPE}/api/admin/role-bindings"
rows = c.get(base).json()
if sys.argv[1] == "add":
    group, role = sys.argv[2], sys.argv[3]
    if not any(b["principal_type"] == "group" and b["principal"] == group and b["role"] == role for b in rows):
        print("add", group, role, c.post(base, json={"principal_type": "group", "principal": group, "role": role}).status_code)
    rows = c.get(base).json()
for b in rows:
    print(b["id"], b["principal_type"], b["principal"], b["role"])
