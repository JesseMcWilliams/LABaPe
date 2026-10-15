#!/usr/bin/env bash
# mail_check.sh ENV_ID VM
# The environment's mail server (M1, design §50.6), from the worker: sends a test message to VM:25
# with STARTTLS (checked against the platform CA), then reads it back through Mailpit's API (HTTPS
# 8025, user mailpit, the stored password) and checks the API refuses a request without it. Prints
# statuses and the subject found, never the password.
set -euo pipefail
[ $# -ge 2 ] || { sed -n '2,7p' "$0" >&2; exit 2; }
W="$(docker ps --format '{{.Names}}' | grep -m1 'labape-worker')"
docker exec -i -e ENV_ID="$1" -e VM="$2" "$W" python3 - <<'PY'
import os
import smtplib
import ssl
import time
import uuid
from email.message import EmailMessage

import httpx

from labape import mail_server
from labape.db import session_factory
from labape.engine.vmaccess import _hosts, _inventory
from labape.models import Environment
from labape.security import get_secret

env_id, vm = int(os.environ["ENV_ID"]), os.environ["VM"]
with session_factory()() as db:
    env = db.get(Environment, env_id)
    addr = str(_hosts(_inventory(db, env))[vm]["ansible_host"])
    pw = get_secret(db, mail_server.ui_secret(env_id, vm)) or ""
from labape import ca
ctx = ca.trust()                                 # the platform root
ctx.check_hostname = False                       # the certificate names the VM, reached here by address
subject = f"LABaPe M1 check {uuid.uuid4().hex[:8]}"
msg = EmailMessage()
msg["From"], msg["To"], msg["Subject"] = "labape@lab.test", "someone@lab.test", subject
msg.set_content("A test message from LABaPe's mail_check.sh.")
with smtplib.SMTP(addr, 25, timeout=20) as s:
    s.ehlo()
    tls = s.has_extn("starttls")
    if tls:
        s.starttls(context=ctx)
        s.ehlo()
    s.send_message(msg)
print("smtp: sent", "with STARTTLS" if tls else "WITHOUT STARTTLS")
base = f"https://{addr}:8025/api/v1"
with httpx.Client(verify=False, timeout=20) as c:
    print("api without a password:", c.get(f"{base}/messages").status_code)
    found = False
    for _ in range(10):
        r = c.get(f"{base}/messages", auth=("mailpit", pw))
        found = any(m.get("Subject") == subject for m in (r.json().get("messages") or [])) if r.status_code == 200 else False
        if found:
            break
        time.sleep(2)
    print("api with the password:", r.status_code, "message found:", found, subject if found else "")
PY
