"""Best-fit placement (design §43): rank the KVM hosts for a one-group environment, or create one
with `kvm_host_id: null` and wait for its deploy.

Usage: placement.py [preview] | create NAME
Uses a Debian template group (debian_latest, debian13-packer-2026.10). Signs in as labape-admin.
"""
import sys
import time

from oidc_login import LABAPE, login

c = login("labape-admin")
group = {"name": "web", "os": "debian_latest", "roles": ["linux_server"], "count": 1, "cpu_count": 1,
         "memory_mb": 1024, "disk_gb": 20, "image_source": "packer_template", "template": "debian13-packer-2026.10"}
for h in c.post(f"{LABAPE}/api/environments/placement", json={"host_groups": [group]}).json()["hosts"]:
    print(f"  {h['name']}: ok={h['ok']} score={h['score']} {h['reasons']}")
if sys.argv[1:2] == ["create"]:
    r = c.post(f"{LABAPE}/api/environments", json={"name": sys.argv[2], "kvm_host_id": None, "host_groups": [group]})
    print("create:", r.status_code, r.json().get("environment", {}).get("kvm_host") if r.status_code == 201 else r.text[:300])
    jid = r.json().get("job_id") if r.status_code == 201 else None
    while jid:
        j = c.get(f"{LABAPE}/api/jobs/{jid}").json()
        if j["state"] in ("succeeded", "failed", "cancelled"):
            print(f"job {jid} {j['state']}; environment {r.json()['environment']['id']}")
            break
        time.sleep(10)
