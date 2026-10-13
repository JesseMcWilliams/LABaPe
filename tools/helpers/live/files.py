"""List the Files tab through the API (design §21.3, §28.2).

Usage: files.py [isos|installers|files] [--targets]
Prints each file's name, size and SHA-256 prefix; installers also show their
OS targets. --targets prints the target vocabulary.
"""
import sys

from oidc_login import LABAPE, login

c = login("labape-admin")
if "--targets" in sys.argv:
    for t in c.get(f"{LABAPE}/api/files/targets").json():
        print(f"  {t['group']:<12} {t['value']:<22} {t['label']}")
    sys.exit(0)
cat = next((a for a in sys.argv[1:] if not a.startswith("--")), "installers")
rows = c.get(f"{LABAPE}/api/files", params={"category": cat}).json()
print(f"{cat}: {len(rows)} file(s)")
for f in rows:
    extra = f" targets={f.get('targets')}" if cat == "installers" else (f" os={f['os_key']}" if cat == "isos" else "")
    print(f"  {f['name']}  {f['size'] // 2**20} MB  {f['sha256'][:12]}{extra}")
