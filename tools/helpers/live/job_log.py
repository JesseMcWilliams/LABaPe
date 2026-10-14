"""Print a finished job's log lines that mention a host, with their task headers.

Usage: job_log.py <job id> [host]
Without a host, prints the whole log. Ansible's no_log hides secret values.
"""
import sys

from oidc_login import job_log, login

admin = login("labape-admin")
log = job_log(admin, sys.argv[1]).splitlines()
host = sys.argv[2] if len(sys.argv) > 2 else ""
task = ""
for line in log:
    if line.startswith(("TASK [", "PLAY [")):
        task = line
        continue
    if not host or f"[{host}]" in line or f"{host} " in line:
        if task:
            print(task)
            task = ""
        print(line[:400])
