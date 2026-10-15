"""A KVM host's files path (F1, design §50.3), through the API as labape-admin.

Usage: host_files.py <host id> [new path|default]
Without a path, prints the host's files path, the folder it uses now and its free-space alert. With
one, saves it (`default`: beside the VM folder again), which creates the folders through the access
account and queues a job moving the files; waits for that job and prints its log.
"""
import sys
import time

from oidc_login import LABAPE, job_log, login

c = login("labape-admin")
hid = sys.argv[1]
h = c.get(f"{LABAPE}/api/hosts/{hid}").json()
print("files_path:", repr(h.get("files_path")), "uses:", h.get("files_dir"), "free-space alert:", h.get("disk_free_pct"))
if len(sys.argv) < 3:
    raise SystemExit
new = "" if sys.argv[2] == "default" else sys.argv[2]
body = {k: v for k, v in h.items() if k not in ("id", "resources", "files_dir", "access_status", "access_error",
                                                   "access_at", "log_forward", "log_forward_until", "log_forward_error")}
body["files_path"] = new
r = c.put(f"{LABAPE}/api/hosts/{hid}", json=body)
print("save:", r.status_code, r.json().get("files_dir") if r.status_code == 200 else r.text[:300])
job = r.json().get("files_move_job") if r.status_code == 200 else None
if job:
    while c.get(f"{LABAPE}/api/jobs/{job}").json()["state"] in ("queued", "running"):
        time.sleep(3)
    print("job", job, c.get(f"{LABAPE}/api/jobs/{job}").json()["state"])
    print(job_log(c, job).strip())
