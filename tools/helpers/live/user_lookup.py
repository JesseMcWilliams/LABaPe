"""Question 196 against the real Authentik: what the provider check says about accounts (statuses only).

Usage (on the lab host, in the API container):
  docker compose exec -T labape-api python /tools/user_lookup.py [username ...]
With no names it checks every user that owns a live personal token and one made-up name.
Reads only; changes nothing (it does not run the sweep).
"""
import sys

from labape import userstatus
from labape.db import session_factory
from labape.models import User

with session_factory()() as db:
    names = sys.argv[1:] or ["labape-admin", "labape-viewer", "zz-nobody-here"]
    for n in names:
        u = db.query(User).filter_by(username=n).first() or User(provider="oidc", subject="x", username=n)
        print(f"{n:20} -> {userstatus.lookup(db, u)}")
