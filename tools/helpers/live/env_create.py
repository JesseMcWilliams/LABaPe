"""Create a one-VM environment through the API and wait for its deploy.

Usage: env_create.py list
       env_create.py <name> <os key> <template> [--vm NAME] [--cpu N] [--mem MB] [--disk GB]
                     [--network NAME] [--addressing static|dhcp] [--host ID] [--as USER]
                     [--apps NAME,NAME] [--services NAME,NAME]
`list` prints the KVM hosts, the templates and networks of each, and the
environments. Otherwise it creates the environment (a deploy job starts at
once), waits, and prints the result, the VM's address and the job's
labape:/fatal:/PLAY RECAP lines. Signs in as labape-admin unless --as names
another test user (who then owns it).
"""
import argparse
import sys
import time

from oidc_login import LABAPE, job_log, login

if sys.argv[1:2] == ["list"]:
    c = login("labape-admin")
    for h in c.get(f"{LABAPE}/api/hosts").json():
        print(f"host {h['id']} {h['name']} enabled={h['enabled']}")
        for t in c.get(f"{LABAPE}/api/templates?kvm_host_id={h['id']}").json():
            print(f"  template {t['name']} os={t.get('os_key')} disk={t.get('disk_gb')}")
        for n in c.get(f"{LABAPE}/api/networks?kvm_host_id={h['id']}").json():
            print(f"  network {n['name']} {n['cidr']} {n.get('addressing')} default={n.get('is_default')}")
    for e in c.get(f"{LABAPE}/api/environments").json():
        print(f"environment {e['id']} {e['name']} {e['status']}")
    sys.exit(0)

ap = argparse.ArgumentParser()
for a in ("name", "os", "template"):
    ap.add_argument(a)
ap.add_argument("--vm", default="")
ap.add_argument("--cpu", type=int, default=2)
ap.add_argument("--mem", type=int, default=4096)
ap.add_argument("--disk", type=int, default=40)
ap.add_argument("--network", default=None)
ap.add_argument("--addressing", default="static")
ap.add_argument("--host", type=int, default=None)
ap.add_argument("--as", dest="user", default="labape-admin")
ap.add_argument("--apps", default="", help="catalog applications, comma-separated")
ap.add_argument("--services", default="", help="catalog services, comma-separated")
o = ap.parse_args()

c = login(o.user)
host = o.host or c.get(f"{LABAPE}/api/hosts").json()[0]["id"]
role = "windows_server" if o.os.startswith("windows") else "linux_server"
group = {"name": "".join(ch for ch in o.name if ch.isalnum())[:12] or "vm", "count": 1, "os": o.os, "roles": [role],
         "image_source": "packer_template", "template": o.template, "cpu_count": o.cpu, "memory_mb": o.mem,
         "disk_gb": o.disk, "windows_core": False, "addressing": o.addressing, "names": [o.vm],
         "services": [s for s in o.services.split(",") if s], "applications": [a for a in o.apps.split(",") if a], "packages": []}
if o.network:
    group["network"] = o.network
r = c.post(f"{LABAPE}/api/environments", json={"name": o.name, "kvm_host_id": host, "host_groups": [group]})
if r.status_code >= 300:
    raise SystemExit(f"create refused: {r.status_code} {r.text[:600]}")
env = r.json()["environment"]
job = r.json().get("job_id") or max(j["id"] for j in c.get(f"{LABAPE}/api/jobs").json()
                                    if j.get("environment_id") == env["id"])
print("environment", env["id"], o.name, "job", job)
start = time.time()
while True:
    time.sleep(20)
    try:
        j = c.get(f"{LABAPE}/api/jobs/{job}").json()
    except Exception:  # noqa: BLE001 — sign in again if the session ended
        c = login(o.user)
        continue
    if j["state"] not in ("queued", "running"):
        break
print("job:", job, j["state"], f"{int(time.time() - start)}s")
print("addresses:", c.get(f"{LABAPE}/api/environments/{env['id']}").json().get("addresses"))
log = job_log(c, job).splitlines()
keep = [line for line in log if line.startswith(("labape:", "PLAY RECAP", "fatal:", "ERROR")) or " : ok=" in line]
print("\n".join(keep[-30:]))
