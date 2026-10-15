"""What is installed in LABaPe's worker image, for tools/helpers/sbom.py (design: Claude_Docs/Reference_SBOM.md).

Run inside the worker container with its venv Python and save the output as sbom/image-snapshot.json:
  docker exec -i <labape-worker> /opt/labape/venv/bin/python - < tools/helpers/sbom_probe.py
Prints one JSON object: the venv's Python distributions (name, version, license, summary, requirements, and
whether Debian or pip installed it) and the explicitly installed Debian packages. Changes nothing.
"""
import json
import subprocess
from importlib import metadata

out = {"python": [], "apt": [], "os": ""}
for d in sorted(metadata.distributions(), key=lambda x: (x.metadata["Name"] or "").lower()):
    m = d.metadata
    lic = m.get("License-Expression") or ""
    if not lic:
        lic = m.get("License") or ""
        if len(lic) > 60 or "\n" in lic:
            lic = ""
    if not lic:
        cl = [c.split("::")[-1].strip() for c in m.get_all("Classifier") or [] if c.startswith("License ::")]
        lic = " / ".join(cl)
    req = [r.split(";")[0].strip() for r in (d.requires or []) if "extra ==" not in r]
    out["python"].append({"name": m["Name"], "version": d.version, "license": lic or "unknown",
                          "summary": (m.get("Summary") or "")[:140], "home": m.get("Home-page") or "",
                          "requires": req, "origin": "debian" if "dist-packages" in str(d._path) else "pip"})
want = ("bash ca-certificates curl unzip git openssh-client perl iputils-ping python3 python3-venv libvirt-clients "
        "virtinst qemu-utils xorriso qemu-system-x86 ovmf postgresql-client").split()
for p in want:
    r = subprocess.run(["dpkg-query", "-W", "-f", "${Package}\t${Version}\t${Architecture}\n", p], capture_output=True, text=True)
    out["apt"].append(r.stdout.strip() or f"{p}\t?\t?")
out["os"] = open("/etc/os-release").read().split("PRETTY_NAME=")[1].split("\n")[0].strip('"')
out["dpkg_total"] = len(subprocess.run(["dpkg-query", "-W", "-f", "${Package}\n"], capture_output=True, text=True).stdout.split())
print(json.dumps(out))
