"""VMs reading their own secrets (V2, design §49.3), as labape-admin.

Usage: vm_secrets.py ENV_ID shared NAME VM[,VM...]   a shared secret only those VMs may read
       vm_secrets.py ENV_ID own NAME VM              one VM's own secret
       vm_secrets.py ENV_ID delete NAME [VM]
       vm_secrets.py sync                            ask for OpenBao's policies now (people and VMs)
Values are random; prints the first 12 hex digits of each value's SHA-256, for comparing what a VM
reads (`sudo labape-secret get NAME | tr -d '\\n' | sha256sum`), never the value.
"""
import hashlib
import secrets
import sys
import time

from oidc_login import LABAPE, login

c = login("labape-admin")
if sys.argv[1] == "sync":
    r = c.post(f"{LABAPE}/api/admin/openbao-people/sync")
    print("sync", r.status_code, r.json().get("changes", r.text[:300]))
    time.sleep(20)
    raise SystemExit
env_id, what, name = sys.argv[1], sys.argv[2], sys.argv[3]
base = f"{LABAPE}/api/environments/{env_id}/secrets"
if what == "delete":
    vm = sys.argv[4] if len(sys.argv) > 4 else ""
    rest = f"uservm/{vm}/{name}" if vm else f"user/{name}"
    print("delete", rest, c.delete(base, params={"name": rest}).status_code)
    raise SystemExit
value = secrets.token_urlsafe(24)
body = {"name": name, "value": value}
if what == "shared":
    body["readers"] = sys.argv[4].split(",")
else:
    body["vm"] = sys.argv[4]
r = c.put(base, json=body)
print(what, name, r.status_code, r.json(), "sha256:", hashlib.sha256(value.encode()).hexdigest()[:12])
