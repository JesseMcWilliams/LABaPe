"""A VM's details page through the API (design §28.6).

Usage: vm_details.py show ENV_ID VM                    the details: state, guest agent facts, series, consoles
       vm_details.py accounts ENV_ID VM                local accounts and groups, read live
       vm_details.py roundtrip ENV_ID VM               add a test account and group, put it in, lock,
                                                       unlock, set its password, then delete both
Signs in as labape-admin. The roundtrip's password is the documented fake placeholder.
"""
import sys

from oidc_login import LABAPE, login

PW = "ThisIsMy_FAKE_Password6!"
c = login("labape-admin")
cmd, env_id, vm = sys.argv[1], int(sys.argv[2]), sys.argv[3]
base = f"{LABAPE}/api/environments/{env_id}/vms/{vm}"

if cmd == "show":
    d = c.get(base).json()
    g = d.get("guest") or {}
    print(f"{d['name']} ({d['os']}, group {d['group']}): {d['state']}, agent {d['responding']}, address {d['address']}")
    print(f"  size {d['size']}")
    print(f"  guest os {g.get('os', {}).get('pretty-name')} host {g.get('hostname')} tz {g.get('timezone')}")
    print(f"  users {[u['name'] for u in g.get('users', [])]}")
    for f in g.get("filesystems", []):
        print(f"  fs {f['mount']} {f['name']} {f['type']} total={f['total']} used={f['used']}")
    for i in g.get("interfaces", []):
        print(f"  nic {i['name']} {i['hwaddr']} {i['addresses']}")
    print(f"  series points {len(d['series']['t'])}; consoles {d['consoles']}")
elif cmd == "accounts":
    r = c.get(f"{base}/accounts")
    if r.status_code != 200:
        raise SystemExit(f"{r.status_code} {r.text[:300]}")
    d = r.json()
    print(f"windows={d.get('windows')} note={d.get('note', '')}")
    for a in d["accounts"]:
        print(f"  user {a['name']} groups={a.get('groups')} admin={a.get('admin')} locked={a.get('locked')}")
    for g in d["groups"]:
        print(f"  group {g['name']} members={g.get('members')}")
elif cmd == "roundtrip":
    windows = c.get(base).json()["os"].startswith("windows")
    user, group = ("LabTest User", "LabTest Group") if windows else ("labtest", "labtestgrp")
    steps = [("user.add", user, {"comment": "LABaPe live test", **({"password": PW} if windows else {})}),
             ("group.add", group, {}), ("group.member_add", group, {"member": user}),
             ("user.lock", user, {}), ("user.unlock", user, {}), ("user.password", user, {"password": PW})]
    for op, name, extra in steps:
        r = c.post(f"{base}/accounts", json={"op": op, "name": name, **extra})
        print(f"  {op} {name}: {r.status_code} {'' if r.status_code == 200 else r.text[:300]}")
    d = c.get(f"{base}/accounts").json()
    acct = next((a for a in d["accounts"] if a["name"] == user), None)
    grp = next((g for g in d["groups"] if g["name"] == group), None)
    print(f"  read back: user={acct and acct.get('locked') is False} in group={bool(grp and user in grp['members'])}")
    for op, name in (("group.delete", group), ("user.delete", user)):
        r = c.post(f"{base}/accounts", json={"op": op, "name": name})
        print(f"  {op} {name}: {r.status_code} {'' if r.status_code == 200 else r.text[:300]}")
    r = c.post(f"{base}/accounts", json={"op": "user.delete", "name": "Administrator" if windows else "root"})
    print(f"  protected delete refused: {r.status_code == 422}")
    audit = c.get(f"{LABAPE}/api/admin/audit?action=vm.user.password").text
    print(f"  password kept out of the audit log: {PW not in audit}")
