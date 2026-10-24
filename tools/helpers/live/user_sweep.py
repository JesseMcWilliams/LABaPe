"""Question 196 end to end on the real stack: a throwaway account that Authentik doesn't know owns a personal
token; the sweep disables it and the token stops working. Removes the account afterwards.

Usage (in the API container): python /tmp/user_sweep.py [BASE_URL]   (default http://localhost:8000, the API itself)
"""
import sys

import httpx

from labape import tokens, userstatus
from labape.db import session_factory
from labape.models import ApiToken, User

base = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000"
with session_factory()() as db:
    for old in db.query(User).filter_by(subject="zz-ghost-sweep"):      # left by an earlier run that stopped
        db.query(ApiToken).filter_by(user_id=old.id).delete()
        db.delete(old)
    db.commit()
    ghost = User(provider="oidc", subject="zz-ghost-sweep", username="zz-ghost", display_name="zz ghost", groups=["labape-admins"])
    db.add(ghost)
    db.commit()
    tok, secret = tokens.create(db, ghost, "zz-sweep-test", "personal", ["admin"], 1)
    h = {"Authorization": f"Bearer {secret}"}
    print("token before :", httpx.get(f"{base}/api/hosts", headers=h, verify=False).status_code)
    print("sweep        :", userstatus.sweep(db))
    db.refresh(ghost)
    print("disabled     :", ghost.disabled_at is not None, ghost.disabled_reason)
    print("token after  :", httpx.get(f"{base}/api/hosts", headers=h, verify=False).status_code, "(401 expected)")
    db.query(ApiToken).filter_by(user_id=ghost.id).delete()
    db.delete(ghost)
    db.commit()
    print("cleaned up")
