"""Run an environment action and wait for it.

Usage: env_job.py <environment id> <deploy|refresh-addresses|destroy>
Prints the job's result, duration, and its labape:, fatal:, ERROR and PLAY
RECAP lines (the last 40). A deploy takes 5-20 minutes; destroy really
destroys the environment's VMs.
"""
import sys
import time

from oidc_login import LABAPE, job_log, login

admin = login("labape-admin")
env_id, action = sys.argv[1], sys.argv[2]
r = admin.post(f"{LABAPE}/api/environments/{env_id}/{action}")
print(action, r.status_code, r.json())
job = r.json()["job_id"]
start = time.time()
while True:
    time.sleep(20)
    try:
        j = admin.get(f"{LABAPE}/api/jobs/{job}").json()
    except Exception:  # noqa: BLE001 — sign in again if the session ended
        admin = login("labape-admin")
        continue
    if j["state"] not in ("queued", "running"):
        break
print("job:", job, j["state"], f"{int(time.time() - start)}s")
log = job_log(admin, job).splitlines()
keep = [line for line in log if line.startswith(("labape:", "PLAY RECAP", "fatal:", "ERROR")) or " : ok=" in line]
print("\n".join(keep[-40:]))
