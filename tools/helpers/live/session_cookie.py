"""Sign in as a test user and print the labape_session cookie's value (and nothing else), for the browser tests'
`LABAPE_SESSION_COOKIE` (web/e2e/README.md). Treat the output like a password: redirect it to a file.

Usage: session_cookie.py [username]   (default labape-admin)
"""
import sys

from oidc_login import LABAPE, login

c = login(sys.argv[1] if len(sys.argv) > 1 else "labape-admin")
assert c.get(f"{LABAPE}/api/auth/me").status_code == 200
print(c.cookies.get("labape_session"))
