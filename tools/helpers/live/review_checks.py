"""Live checks for the 2026-10-23 review's decisions (questions 196-202) against the real stack.

Usage: review_checks.py users | download | share ENV_ID
  users     the Users list; disable labape-viewer, see that its session and the page stop, enable it again
  download  Download from URL refuses loopback, localhost, a single-label name and the container network
  share     share ENV_ID (owned by labape-admin) with labape-viewer, see it as the viewer, unshare; then hand it to
            labape-dev and back
Signs in as the test users; prints statuses only.
"""
import sys

from oidc_login import LABAPE, login

what = sys.argv[1] if len(sys.argv) > 1 else ""
admin = login("labape-admin")


def row(name):
    for u in admin.get(f"{LABAPE}/api/admin/users", params={"q": name}).json()["users"]:
        if u["username"] == name:
            return u
    raise SystemExit(f"{name} has not signed in")


if what == "users":
    viewer = login("labape-viewer")
    print("viewer /me before:", viewer.get(f"{LABAPE}/api/auth/me").status_code)
    u = row("labape-viewer")
    print("row:", {k: u[k] for k in ("username", "roles", "disabled", "active_tokens", "open_sessions", "last_login")})
    print("disable:", admin.post(f"{LABAPE}/api/admin/users/{u['id']}/disable").json())
    print("viewer /me while disabled:", viewer.get(f"{LABAPE}/api/auth/me").status_code, "(401 expected)")
    print("disabled list:", [x["username"] for x in admin.get(f"{LABAPE}/api/admin/users", params={"disabled": "true"}).json()["users"]])
    print("sign in while disabled:", end=" ")
    try:
        login("labape-viewer")
        print("signed in?!")
    except BaseException as exc:  # noqa: BLE001
        print("refused" if "403" in str(exc) or "disabled" in str(exc).lower() or isinstance(exc, (SystemExit, AssertionError)) else exc)
    print("enable:", admin.post(f"{LABAPE}/api/admin/users/{u['id']}/enable").json())
    print("viewer signs in again:", login("labape-viewer").get(f"{LABAPE}/api/auth/me").status_code)
    print("self-disable:", admin.post(f"{LABAPE}/api/admin/users/{row('labape-admin')['id']}/disable").status_code, "(409 expected)")

elif what == "download":
    for url in ("https://127.0.0.1:8200/x.iso", "https://localhost/x.iso", "https://labape-openbao:8200/x.iso",
                "https://[::1]/x.iso", "https://169.254.169.254/x.iso", "https://10.255.255.1/x.iso"):
        r = admin.post(f"{LABAPE}/api/files/download", json={"category": "files", "url": url, "targets": []})
        print(f"{url:42} -> {r.status_code} {(r.json().get('detail') if r.headers.get('content-type','').startswith('application/json') else r.text)!s:.150}")
    import subprocess
    print("(the last one is allowed by the check; any job it queued is cancelled below)")
    for j in admin.get(f"{LABAPE}/api/jobs").json()[:3]:
        if "download" in j["type"] and j["state"] in ("queued", "running"):
            print("cancel", j["id"], admin.post(f"{LABAPE}/api/jobs/{j['id']}/cancel").status_code)

elif what == "share":
    env = sys.argv[2]
    base = f"{LABAPE}/api/environments/{env}"
    viewer = login("labape-viewer")
    print("viewer sees before:", [e["id"] for e in viewer.get(f"{LABAPE}/api/environments").json()])
    r = admin.post(f"{base}/sharing", json={"principal_type": "user", "principal": "labape-viewer"})
    print("share:", r.status_code, r.text[:100])
    print("viewer sees after:", [e["id"] for e in viewer.get(f"{LABAPE}/api/environments").json()])
    print("viewer opens it:", viewer.get(base).status_code, "| viewer sharing page:", viewer.get(f"{base}/sharing").status_code, "(403 expected)")
    grants = admin.get(f"{base}/sharing").json()["grants"]
    print("grants:", [(g["principal"], g["level"]) for g in grants])
    gid = next(g["id"] for g in grants if g["principal"] == "labape-viewer")
    print("unshare:", admin.delete(f"{base}/sharing/{gid}").status_code)
    print("viewer sees after unshare:", [e["id"] for e in viewer.get(f"{LABAPE}/api/environments").json()])
    login("labape-dev")
    print("hand to labape-dev:", admin.post(f"{base}/transfer", json={"username": "labape-dev"}).status_code)
    dev = login("labape-dev")
    print("dev is owner:", dev.get(base).json().get("is_owner"), "| admin grants:", [(g["principal"], g["level"]) for g in admin.get(f"{base}/sharing").json()["grants"]])
    print("hand back:", dev.post(f"{base}/transfer", json={"username": "labape-admin", "keep_access": False}).status_code)
    print("grants now:", [(g["principal"], g["level"]) for g in admin.get(f"{base}/sharing").json()["grants"]])
else:
    sys.exit(__doc__)
