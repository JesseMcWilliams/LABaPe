"""The stack's mail server, Mailpit (design §52), through LABaPe as labape-admin.

Usage: stack_mail.py status
       stack_mail.py route                       is Mailpit's page admin-only at /mail/?
       stack_mail.py test ADDRESS                send a test email the way LABaPe sends its own
       stack_mail.py inbox [N]                   the newest N messages Mailpit holds (via /mail/api, as an admin)
       stack_mail.py relay HOST PORT SECURITY listed|all [ALLOW,...]   forward through HOST (SECURITY: none|starttls|ssl)
       stack_mail.py off                         stop forwarding
Waits for Mailpit to pick up changed options. Prints no passwords.
"""
import sys
import time

import httpx

from oidc_login import LABAPE, login

c = login("labape-admin")
cmd = sys.argv[1] if len(sys.argv) > 1 else "status"


def settings() -> dict:
    return c.get(f"{LABAPE}/api/admin/settings").json()["stack_mail"]


def status() -> dict:
    return c.get(f"{LABAPE}/api/admin/mail").json()


def wait_applied() -> None:
    for _ in range(20):
        if status()["applied"]:
            print("Mailpit runs with the saved options")
            return
        time.sleep(3)
    print("Mailpit did not report the options as applied")


if cmd == "status":
    s = status()
    print({k: s[k] for k in ("available", "reachable", "version", "messages", "applied", "email_uses_mailpit")})
    print("email:", s["email"], "alerts to:", s["alert_recipients"])
    print("relay:", {k: v for k, v in settings().items() if k != "has_password"})
elif cmd == "route":
    anon = httpx.Client(verify=False, follow_redirects=False, timeout=20)
    r = anon.get(f"{LABAPE}/mail/")
    print("signed out:", r.status_code)
    r = c.get(f"{LABAPE}/mail/", follow_redirects=True)
    print("admin /mail/:", r.status_code, r.headers.get("content-type", "")[:30], "Mailpit" in r.text)
    r = c.get(f"{LABAPE}/mail/api/v1/info")
    print("admin /mail/api/v1/info:", r.status_code, r.json().get("Version") if r.status_code == 200 else r.text[:80])
elif cmd == "test":
    r = c.post(f"{LABAPE}/api/admin/mail/test", json={"to": sys.argv[2]})
    print(r.status_code, r.json().get("message", r.text[:200]))
elif cmd == "inbox":
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 5
    for m in c.get(f"{LABAPE}/mail/api/v1/messages?limit={n}").json()["messages"]:
        print(f"  {m['Created'][:19]}  to={[t['Address'] for t in m['To']]}  {m['Subject']}")
elif cmd in ("relay", "off"):
    cur = settings()
    if cmd == "off":
        cur["relay"] = False
    else:
        host, port, security, mode = sys.argv[2:6]
        cur.update(relay=True, host=host, port=int(port), security=security, forward=mode, username="", verify_tls=True,
                   allow=sys.argv[6].split(",") if len(sys.argv) > 6 else [])
    cur.pop("has_password", None)
    r = c.put(f"{LABAPE}/api/admin/settings/stack_mail", json=cur)
    print("save:", r.status_code, r.text[:200] if r.status_code != 200 else "")
    wait_applied()
