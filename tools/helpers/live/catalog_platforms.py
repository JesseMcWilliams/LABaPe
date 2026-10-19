"""The catalog's items and the OS families each supports (design §21.4): packages get theirs from their members.

Usage: catalog_platforms.py [kind]
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
kind = sys.argv[1] if len(sys.argv) > 1 else "package"
for i in c.get(f"{LABAPE}/api/catalog", params={"kind": kind}).json():
    members = ", ".join(f"{m['kind']}:{m['name']}" for m in (i["definition"].get("items") or []))
    print(f"{i['name']:24} {i['status']:9} {','.join(i['platforms']) or '-':14} {members}")
