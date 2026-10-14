"""Full-stack backups (design §32): list the archives, or ask the update agent for one and wait.

Usage: stack_backup.py [list] | run [--wait]
Signs in as labape-admin.
"""
import sys
import time

from oidc_login import LABAPE, login

c = login("labape-admin")
cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
if cmd == "run":
    r = c.post(f"{LABAPE}/api/admin/backups/stack")
    print("request:", r.status_code, r.text[:300])
    if r.status_code != 200:
        sys.exit(1)
    if "--wait" in sys.argv:
        for _ in range(120):
            time.sleep(10)
            st = c.get(f"{LABAPE}/api/admin/backups/stack").json()["status"] or {}
            if st.get("state") in ("succeeded", "failed") and not st.get("pending"):
                break
m = c.get(f"{LABAPE}/api/admin/backups/stack").json()
print(f"directory {m['directory']}; agent installed={m['agent']['installed']} user={m['service_user']}; "
      f"key file {m['key_file'] or '-'}; key saved: {m['key_saved'] or 'not yet'}")
if m["status"]:
    print(f"agent: [{m['status'].get('state')}] {m['status'].get('message', '')}")
for a in m["archives"]:
    ck = a.get("check") or {}
    print(f"  {a['archive']}  {a['size'] / 1048576:.1f} MB  {a['created_at'][:19]}  {a.get('version')}  "
          f"check={'ok' if ck.get('ok') else 'FAILED'} restores={ck.get('restores')} problems={ck.get('problems')}")
