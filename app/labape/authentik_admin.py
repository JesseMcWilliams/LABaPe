"""The Authentik administrator's password (design §54): sign in as akadmin through Authentik's flow executor.

The password the stack's Authentik was started with (AUTHENTIK_BOOTSTRAP_PASSWORD) is kept in the secret
store, checked by really signing in, and rotated through the Authentik API with an elevated account.
"""
from __future__ import annotations

import httpx

USER = "akadmin"
SECRET = "settings/authentik_admin_password"
FLOW = "default-authentication-flow"


def signs_in(base: str, username: str, password: str, verify=True, timeout: float = 15.0) -> bool:
    """True when `username` can sign in to Authentik at `base` with `password`; False when Authentik refuses it.
    Network problems and unexpected answers raise httpx.HTTPError / RuntimeError (the caller decides what they mean)."""
    url = f"{base.rstrip('/')}/api/v3/flows/executor/{FLOW}/"
    with httpx.Client(verify=verify, timeout=timeout, follow_redirects=True) as c:
        r = c.get(url)
        r.raise_for_status()
        challenge = r.json()

        def post(body: dict) -> dict:
            headers = {}
            if c.cookies.get("authentik_csrf"):
                headers["X-authentik-CSRF"] = c.cookies.get("authentik_csrf")
            resp = c.post(url, json=body, headers=headers)
            resp.raise_for_status()
            return resp.json()

        for _ in range(6):
            comp = challenge.get("component")
            if comp == "ak-stage-identification":
                challenge = post({"component": comp, "uid_field": username})
            elif comp == "ak-stage-password":
                challenge = post({"component": comp, "password": password})
                if challenge.get("component") == "ak-stage-password":      # asked again: it was refused
                    return False
            elif comp == "xak-flow-redirect":
                return True
            else:
                raise RuntimeError(f"Authentik's sign-in flow asked for something else ({comp})")
        raise RuntimeError("Authentik's sign-in flow did not finish")
