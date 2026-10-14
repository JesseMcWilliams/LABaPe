"""Sign in to a stack's LABaPe with a linked stack's account (design §47.2), without a browser.

Usage: linked_login.py SOURCE_NAME USER
Environment: LABAPE (the stack signed in to), AK_ENV (the linked stack's tools/authentik-test/.env,
which holds its test users' password). Run it where that .env is, e.g. on the linked stack's host:
  docker run --rm --network host -v .../tools/helpers/live:/tools:ro -v .../authentik-test/.env:/authentik.env:ro \
    -e LABAPE=https://<stack> -e AK_ENV=/authentik.env --entrypoint python3 labape:local /tools/linked_login.py <peer> labape-admin
Follows LABaPe → this stack's Authentik → "Sign in with SOURCE_NAME" → the linked stack's Authentik (user
and password) → back through this stack's source enrollment → LABaPe, then prints /api/auth/me.
"""
import json
import re
import sys
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx

from oidc_login import LABAPE, password, post

source, user = sys.argv[1], sys.argv[2]
c = httpx.Client(verify=False, follow_redirects=False, timeout=30)


def flow(url: str) -> str:
    """Run the Authentik flow at `url` (/if/flow/<slug>/?...) through its executor; returns where it sends us."""
    p = urlparse(url)
    slug = re.match(r"/if/flow/([^/]+)/", p.path).group(1)
    base = f"{p.scheme}://{p.netloc}"
    ex = f"{base}/api/v3/flows/executor/{slug}/?" + urlencode({"query": p.query})
    print(f"  {p.netloc}: flow {slug}")
    r = c.get(ex)
    if r.status_code in (301, 302, 303, 307):            # the flow finished at once: on to where it sends us
        return urljoin(base, r.headers["location"])
    if not r.headers.get("content-type", "").startswith("application/json"):
        raise SystemExit(f"flow {slug} at {p.netloc}: HTTP {r.status_code} {r.text[:300]}")
    ch = r.json()
    for _ in range(8):
        comp = ch.get("component")
        if comp == "ak-stage-identification":
            src = next((s for s in ch.get("sources", []) if s.get("name") == source), None)
            if src is not None and "linked" not in flow.__dict__:
                flow.linked = True                         # this stack's page: take the linked source
                print(f"  {p.netloc}: sign-in page offers {[s['name'] for s in ch.get('sources', [])]}")
                return urljoin(base, src["challenge"]["to"])
            ch = post(c, ex, {"component": comp, "uid_field": user})
        elif comp == "ak-stage-password":
            print(f"  {p.netloc}: password for {user}")
            ch = post(c, ex, {"component": comp, "password": password()})
        elif comp in ("xak-flow-redirect", "ak-stage-autosubmit") and ch.get("to"):
            return urljoin(base, ch["to"])
        elif comp == "ak-stage-autosubmit":
            return ch["url"]
        elif comp == "ak-stage-access-denied":
            raise SystemExit(f"{p.netloc} ({slug}) denied {ch.get('pending_user')}: {ch.get('error_message')}")
        else:
            raise SystemExit(f"unexpected stage at {p.netloc} ({slug}): {json.dumps(ch)[:500]}")
    raise SystemExit(f"flow {slug} at {p.netloc} didn't finish")


url = urljoin(LABAPE, "/api/auth/oidc/login")
for _ in range(40):
    r = c.get(url)
    if r.status_code in (301, 302, 303, 307):
        url = urljoin(url, r.headers["location"])
        if url.startswith(LABAPE + "/") and "/api/auth/oidc/callback" not in url:
            break
        continue
    if re.match(r"/if/flow/", urlparse(url).path):
        url = flow(url)
        continue
    raise SystemExit(f"stuck at {url} {r.status_code} {r.text[:300]}")
me = c.get(f"{LABAPE}/api/auth/me").json()
print("me:", {k: me.get(k) for k in ("username", "groups", "roles")})
