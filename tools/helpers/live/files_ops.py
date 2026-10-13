"""Exercise the Files page through the API: a tus upload, a download from a URL, delete (design §29.5).

Usage: files_ops.py upload CATEGORY NAME SIZE_KB        random bytes, in 1 MB tus chunks
       files_ops.py download CATEGORY URL [NAME]        a files.download job, waited for
       files_ops.py list CATEGORY
       files_ops.py delete ID
Signs in as labape-admin. Prints what the API reports (the stored path says where the file is).
"""
import base64
import os
import sys
import time

from oidc_login import LABAPE, job_log, login

c = login("labape-admin")
cmd, args = sys.argv[1], sys.argv[2:]
TUS = {"Tus-Resumable": "1.0.0"}

if cmd == "upload":
    cat, name, size = args[0], args[1], int(args[2]) * 1024
    data = os.urandom(size)
    meta = ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in {"filename": name, "category": cat}.items())
    r = c.post(f"{LABAPE}/api/files/tus", headers={**TUS, "Upload-Length": str(size), "Upload-Metadata": meta})
    if r.status_code != 201:
        raise SystemExit(f"create: {r.status_code} {r.text[:300]}")
    loc, off, t0 = LABAPE + r.headers["Location"], 0, time.time()
    while off < size:
        part = data[off:off + 2**20]
        r = c.patch(loc, content=part, headers={**TUS, "Upload-Offset": str(off),
                                                 "Content-Type": "application/offset+octet-stream"})
        if r.status_code != 204:
            raise SystemExit(f"patch at {off}: {r.status_code} {r.text[:300]}")
        off = int(r.headers["Upload-Offset"])
    import hashlib
    f = [x for x in c.get(f"{LABAPE}/api/files", params={"category": cat}).json() if x["name"] == name][0]
    print(f"uploaded #{f['id']} {f['path']} {f['size']} bytes in {time.time() - t0:.1f}s; "
          f"sha256 matches: {f['sha256'] == hashlib.sha256(data).hexdigest()}")
elif cmd == "download":
    body = {"category": args[0], "url": args[1], "filename": args[2] if len(args) > 2 else ""}
    r = c.post(f"{LABAPE}/api/files/download", json=body)
    if r.status_code != 200:
        raise SystemExit(f"{r.status_code} {r.text[:300]}")
    job = r.json()["job_id"]
    while (j := c.get(f"{LABAPE}/api/jobs/{job}").json())["state"] in ("queued", "running"):
        time.sleep(3)
    print(f"job {job} {j['state']}")
    for line in [x for x in job_log(c, job).splitlines() if x.startswith("labape:")][-6:]:
        print("  ", line[:200])
elif cmd == "list":
    for f in c.get(f"{LABAPE}/api/files", params={"category": args[0]}).json():
        print(f"  #{f['id']} {f['path']} {f['size']} sha256={f['sha256'][:12]}")
elif cmd == "delete":
    r = c.delete(f"{LABAPE}/api/files/{args[0]}")
    print(r.status_code, r.text[:200])
