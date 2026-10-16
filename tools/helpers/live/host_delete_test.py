"""host_delete_test.py: register a throwaway host, delete it, see it in Deleted hosts, restore it, delete it again and
leave it for the purge (the retention period decides). Touches no real host; `clean` purges leftovers named zz-delete-test."""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
names = lambda: [h["name"] for h in c.get(f"{LABAPE}/api/hosts").json()]      # noqa: E731
if len(sys.argv) > 1 and sys.argv[1] == "clean":
    for d in c.get(f"{LABAPE}/api/hosts/deleted").json():
        if d["name"] == "zz-delete-test":
            print("left in Deleted hosts:", d)
    raise SystemExit
r = c.post(f"{LABAPE}/api/hosts", json={"name": "zz-delete-test", "libvirt_uri": "qemu+ssh://labape@192.0.2.1/system"})
print("register:", r.status_code, r.text[:120] if r.status_code != 201 else "")
if r.status_code == 201:
    hid = r.json()["id"]
    print("listed:", "zz-delete-test" in names())
    r = c.delete(f"{LABAPE}/api/hosts/{hid}")
    print("delete:", r.status_code, r.json())
    print("hidden:", "zz-delete-test" not in names(), "| deleted list:", [d["name"] for d in c.get(f"{LABAPE}/api/hosts/deleted").json()])
    r = c.post(f"{LABAPE}/api/hosts/{hid}/restore")
    print("restore:", r.status_code, "back in list:", "zz-delete-test" in names())
    r = c.delete(f"{LABAPE}/api/hosts/{hid}")
    print("delete again:", r.status_code)
else:
    print("(the lab is at its host limit, so only the refusals are tried)")
# the stack's own host (kvm1, which is also the primary file host) must refuse
for h in c.get(f"{LABAPE}/api/hosts").json():
    if h["is_primary"]:
        r = c.delete(f"{LABAPE}/api/hosts/{h['id']}")
        print("try the primary", h["name"], r.status_code, (r.json().get("detail") or r.text)[:200])
        assert r.status_code == 409, "the primary host must not be deletable"
