"""An environment's own accounts (design §42): list, set, and rotate one now.

Usage: env_accounts.py ENV_ID [list] | set-local NAME ROLE[,ROLE] | add-local NAME ROLE[,ROLE] | set-domain NAME
       | clear | rotate NAME
`set-*` replace the list; `add-local` keeps the accounts there and adds one.
`rotate` runs the account's rotation policy now and waits. The credentials sheet's accounts section
is printed with the passwords masked. Signs in as labape-admin.
"""
import re
import sys
import time

from oidc_login import LABAPE, login

c = login("labape-admin")
env = sys.argv[1]
cmd = sys.argv[2] if len(sys.argv) > 2 else "list"
base = f"{LABAPE}/api/environments/{env}/accounts"
if cmd == "set-local":
    print(c.put(base, json={"accounts": [{"name": sys.argv[3], "kind": "local", "hosts": sys.argv[4].split(",")}]}).status_code)
elif cmd == "add-local":
    have = [a for a in c.get(base).json()["accounts"] if a["name"] != sys.argv[3]]
    print(c.put(base, json={"accounts": have + [{"name": sys.argv[3], "kind": "local", "hosts": sys.argv[4].split(",")}]}).status_code)
elif cmd == "set-domain":
    print(c.put(base, json={"accounts": [{"name": sys.argv[3], "kind": "domain"}]}).status_code)
elif cmd == "clear":
    print(c.put(base, json={"accounts": []}).status_code)
elif cmd == "rotate":
    pols = c.get(f"{LABAPE}/api/admin/secrets").json()
    pols = pols.get("policies", pols) if isinstance(pols, dict) else pols
    p = next(x for x in pols if x["name"].endswith(f".account.{sys.argv[3]}") and x["name"].startswith(f"env.{env}."))
    r = c.post(f"{LABAPE}/api/admin/secrets/{p['id']}/rotate")
    print("rotate:", r.status_code, r.text[:200])
    jid = (r.json() or {}).get("job_id")
    for _ in range(120):
        if not jid:
            break
        j = c.get(f"{LABAPE}/api/jobs/{jid}").json()
        if j["state"] in ("succeeded", "failed", "cancelled"):
            print(f"job {jid} {j['state']}")
            break
        time.sleep(5)
print(c.get(base).json())
sheet = c.get(f"{LABAPE}/api/environments/{env}/credentials").json().get("credentials", "")
part = sheet[sheet.find("## Environment accounts"):] if "## Environment accounts" in sheet else "(no accounts section)"
print(re.sub(r"(Password: )(\S{3})\S+", r"\1\2…", part))
