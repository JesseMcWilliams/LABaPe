"""host_setup.py [apply HOST_ID]: each host's alert and disk setup state (design §83); `apply` queues the job and waits.

`apply` runs the installer's alert and disk steps as root on a real lab host: run it only on purpose.
"""
import sys
import time

from oidc_login import LABAPE, login

c = login("labape-admin")
if len(sys.argv) > 2 and sys.argv[1] == "apply":
    r = c.post(f"{LABAPE}/api/hosts/{sys.argv[2]}/host-setup")
    print(r.status_code, r.text[:200])
    if r.status_code == 200:
        job = r.json()["job_id"]
        while True:
            j = c.get(f"{LABAPE}/api/jobs/{job}").json()
            if j["state"] not in ("queued", "running"):
                break
            time.sleep(5)
        print("job", job, j["state"])
        log = c.get(f"{LABAPE}/api/jobs/{job}/log")
        print(log.text[-3000:] if log.status_code == 200 else f"(log: {log.status_code})")
for h in c.get(f"{LABAPE}/api/hosts").json():
    s = c.get(f"{LABAPE}/api/hosts/{h['id']}/host-setup")
    print(h["id"], h["name"], h.get("access_status"), s.json() if s.status_code == 200 else s.status_code)
