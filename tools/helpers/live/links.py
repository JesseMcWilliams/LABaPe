"""Linked stacks (design §34): list, make a link code, link with a code, check, unlink.

Usage: links.py [list] | invite | join CODE | poll ID | remove ID
`invite` prints only the code (one use, one hour): pass it on without showing it, e.g. into a
variable. Signs in as labape-admin.
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
if cmd == "invite":
    sys.exit(print(c.post(f"{LABAPE}/api/admin/links/invite").json()["code"]))
if cmd == "join":
    r = c.post(f"{LABAPE}/api/admin/links", json={"code": sys.argv[2]})
    print(r.status_code, r.json().get("detail") or r.json().get("url"))
elif cmd == "poll":
    r = c.post(f"{LABAPE}/api/admin/links/{sys.argv[2]}/poll")
    print(r.status_code)
elif cmd == "remove":
    print(c.delete(f"{LABAPE}/api/admin/links/{sys.argv[2]}").status_code)
m = c.get(f"{LABAPE}/api/admin/links").json()
print(f"this stack: {m['self']['url']} (CA {m['self']['ca_fingerprint'][:16]})")
for x in m["links"]:
    s = x["summary"] or {}
    print(f"  #{x['id']} {x['name']} {x['url']} CA {x['ca_fingerprint'][:16]} failures={x['failures']} stale={x['stale']} "
          f"last_ok={str(x['last_ok_at'])[:19]} error={x['error'][:120] or '-'}")
    if s:
        print(f"     {s.get('version')} hosts={[h['name'] for h in s.get('hosts', [])]} envs={len(s.get('environments', []))} "
              f"jobs={s.get('jobs')} alerts={len(s.get('alerts', []))} maintenance={s.get('maintenance', {}).get('active')}")
