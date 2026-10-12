"""Sign in to LABaPe through the test Authentik's flow executor (no browser).

Imported by the other live scripts (`from oidc_login import LABAPE, login`);
run alone it signs in and prints /api/auth/me: `oidc_login.py <username>`.
Environment (set by lab-live.sh): LABAPE (the web interface's URL) and
AK_ENV (tools/authentik-test/.env, which holds the test users' password).
The password is read from there and never printed.
"""
import json
import os
import re
import sys
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

LABAPE = os.environ.get("LABAPE", "").rstrip("/")
ENV = os.environ.get("AK_ENV", "/authentik.env")
if not LABAPE:
    raise SystemExit("set LABAPE to the web interface's URL (lab-live.sh does)")


def password() -> str:
    for line in open(ENV):
        if line.startswith("LABAPE_TEST_USER_PASSWORD="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("no password")


def post(c, url, body):
    headers = {}
    csrf = c.cookies.get("authentik_csrf")
    if csrf:
        headers["X-authentik-CSRF"] = csrf
    r = c.post(url, json=body, headers=headers, follow_redirects=True)
    if not r.headers.get("content-type", "").startswith("application/json"):
        raise SystemExit(f"POST {url} -> {r.status_code} {r.headers.get('content-type')} {r.text[:300]}")
    return r.json()


def login(user: str) -> httpx.Client:
    c = httpx.Client(verify=False, follow_redirects=False, timeout=30)
    r = c.get(f"{LABAPE}/api/auth/oidc/login")
    assert r.status_code in (302, 307), r.status_code
    url = r.headers["location"]
    # Follow redirects until Authentik's flow interface.
    for _ in range(10):
        r = c.get(url)
        if r.status_code in (301, 302, 303, 307):
            url = urljoin(url, r.headers["location"])
            continue
        break
    p = urlparse(url)
    m = re.match(r"/if/flow/([^/]+)/", p.path)
    assert m, f"unexpected page {url} {r.status_code}"
    slug = m.group(1)
    base = f"{p.scheme}://{p.netloc}"
    query = parse_qs(p.query).get("next", [""])[0]
    ex = f"{base}/api/v3/flows/executor/{slug}/?" + urlencode({"query": urlencode({"next": query})})
    ch = c.get(ex).json()
    for _ in range(6):
        comp = ch.get("component")
        if comp == "ak-stage-identification":
            ch = post(c, ex, {"component": comp, "uid_field": user})
        elif comp == "ak-stage-password":
            ch = post(c, ex, {"component": comp, "password": password()})
        elif comp == "xak-flow-redirect":
            url = urljoin(base, ch["to"])
            break
        else:
            raise SystemExit(f"unexpected stage {json.dumps(ch)[:400]}")
    # Back to authorize (implicit consent), then to LABaPe's callback.
    for _ in range(10):
        r = c.get(url)
        if r.status_code in (301, 302, 303, 307):
            url = urljoin(url, r.headers["location"])
            if url.startswith(LABAPE + "/") and "/api/auth/oidc/callback" not in url:
                break
            continue
        # Authentik may answer authorize with its flow UI for consent.
        p = urlparse(url)
        m = re.match(r"/if/flow/([^/]+)/", p.path)
        if m:
            ex = f"{base}/api/v3/flows/executor/{m.group(1)}/?" + urlencode({"query": p.query and urlencode({"next": parse_qs(p.query).get('next', [''])[0]})})
            ch = c.get(ex).json()
            if ch.get("component") == "xak-flow-redirect":
                url = urljoin(base, ch["to"])
                continue
            raise SystemExit(f"unexpected consent stage {json.dumps(ch)[:400]}")
        raise SystemExit(f"stuck at {url} {r.status_code} {r.text[:300]}")
    return c


if __name__ == "__main__":
    c = login(sys.argv[1])
    print("me:", c.get(f"{LABAPE}/api/auth/me").json())
