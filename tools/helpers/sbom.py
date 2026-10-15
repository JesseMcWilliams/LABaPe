#!/usr/bin/env python3
"""Write LABaPe's software bill of materials: Claude_Docs/Reference_SBOM.md (for people) and
sbom/labape.cdx.json (CycloneDX 1.5, for tools).

Usage: python tools/helpers/sbom.py [--check]
  --check   exit 1 when the files on disk differ from what would be written (nothing is written).

Where each part comes from:
  - web (npm): web/package-lock.json, every version and hash;
  - the worker image's Python packages, Debian packages, tools, Ansible collections, OpenTofu providers,
    Packer plugins and the third-party images' digests: sbom/image-snapshot.json, which
    tools/helpers/sbom_snapshot.py takes from the lab stack (tools/helpers/release.sh does it at each
    release). Python's declared requirements are only lower bounds (app/pyproject.toml), so the snapshot
    is the exact record;
  - the declared pins in the repository (container/Dockerfile, compose files, deploy/labape_deploy/steps.py,
    ansible roles): the tables below, kept in step by hand when those change.
Licenses are as the packages and projects declare them; this is not a license or vulnerability audit.
"""
from __future__ import annotations

import ast
import datetime as dt
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
MD = ROOT / "Claude_Docs" / "Reference_SBOM.md"
CDX = ROOT / "sbom" / "labape.cdx.json"
SNAP = ROOT / "sbom" / "image-snapshot.json"

SPDX = {"MIT", "BSD-3-Clause", "BSD-2-Clause", "Apache-2.0", "MPL-2.0", "ISC", "LGPL-3.0-only", "GPL-3.0-or-later",
        "GPL-3.0-only", "MIT-0", "CC-BY-4.0", "LGPL-2.1-or-later", "BUSL-1.1", "PostgreSQL", "Python-2.0", "Unlicense"}
LICENSE_ALIASES = {"Apache 2.0": "Apache-2.0", "MIT License": "MIT", "Apache Software License": "Apache-2.0"}

PY_PURPOSE = {
    "fastapi": "The web API framework (routes, validation, dependency injection).",
    "uvicorn": "The ASGI server that runs the API; the [standard] extra adds httptools, uvloop, watchfiles, websockets and python-dotenv.",
    "SQLAlchemy": "Database access (ORM) for LABaPe's own tables; PostgreSQL in the stack, SQLite in tests.",
    "psycopg": "PostgreSQL driver (psycopg 3, with the compiled binary).",
    "psycopg-binary": "The compiled part of psycopg 3.",
    "Authlib": "OpenID Connect client: sign-in through Authentik (and linked stacks).",
    "httpx": "HTTP client for Authentik, linked stacks, OpenBao, Conjur, downloads and the remote-update checks.",
    "itsdangerous": "Signing, used by Starlette's session cookies.",
    "pydantic-settings": "Reads LABaPe's configuration from the environment.",
    "cryptography": "Fernet encryption of stored secrets, the platform CA's key handling and TLS helpers.",
    "PyYAML": "Reads and writes the YAML LABaPe feeds to Ansible and OpenTofu.",
    "python-multipart": "Parses file and form uploads.",
    "alembic": "Database migrations (the schema upgrades itself at start).",
    "ansible-core": "Runs the playbooks that configure VMs (the engine).",
    "pywinrm": "Lets Ansible manage Windows over WinRM.",
}
WEB_PURPOSE = {
    "react": "UI library.", "react-dom": "Renders React in the browser.", "react-router-dom": "Page routing.",
    "@mui/material": "Component library (Material UI).", "@mui/icons-material": "Icons for Material UI.",
    "@emotion/react": "Styling engine used by Material UI.", "@emotion/styled": "Styled components for Material UI.",
    "guacamole-common-js": "The browser side of Apache Guacamole: RDP, SSH and VNC sessions in the browser.",
    "tus-js-client": "Resumable file uploads (the tus protocol) for Files.",
    "typescript": "Type-checks the UI at build time (build only).", "vite": "Builds and bundles the UI (build only).",
    "@vitejs/plugin-react": "Vite's React support (build only).",
    "@types/react": "Type definitions (build only).", "@types/react-dom": "Type definitions (build only).",
    "@types/guacamole-common-js": "Type definitions (build only).",
}

# (image, role, license, where declared, notes)
IMAGES = [
    ("debian:trixie-slim", "Base of the LABaPe image (API, worker) and of the syslog image", "Various (Debian)", "container/Dockerfile, container/syslog/Dockerfile", "Debian 13; tag only, rebuilt on each image build."),
    ("node:22-bookworm-slim", "Builds the web UI; not in the final image", "MIT (Node.js); various (Debian)", "container/Dockerfile", "Build stage only: the final image carries just the built files."),
    ("caddy:2", "Reverse proxy and HTTPS for the web interface and Authentik", "Apache-2.0", "container/compose.yaml", "Floating major tag."),
    ("smallstep/step-ca:0.30.2", "The platform certificate authority", "Apache-2.0", "container/compose.yaml", ""),
    ("guacamole/guacd:1.6.0", "Guacamole proxy daemon for browser RDP/SSH/VNC", "Apache-2.0", "container/compose.yaml", ""),
    ("postgres:17-alpine", "LABaPe's database, the syslog collector's and Conjur's", "PostgreSQL", "container/compose.yaml", "Three separate containers."),
    ("openbao/openbao:2.7.1", "The secret store (platform secrets, environment secrets, VM sign-in)", "MPL-2.0", "container/compose.yaml", ""),
    ("cyberark/conjur:1.27.0", "Optional second secret store (Conjur Open Source)", "LGPL-3.0", "container/compose.yaml", "Optional (`LABAPE_CONJUR=no` leaves it out)."),
    ("nginx:1.28-alpine", "TLS front for Conjur", "BSD-2-Clause", "container/compose.yaml", "Optional, with Conjur."),
    ("ghcr.io/goauthentik/server:2026.8.3", "Sign-in (OIDC) for the lab's test and single-host installs", "MIT (Authentik's enterprise directory has its own license)", "tools/authentik-test/compose.yaml", "A separate stack beside LABaPe's; its server and worker use this image."),
    ("postgres:16-alpine", "Authentik's database", "PostgreSQL", "tools/authentik-test/compose.yaml", ""),
]
# (tool, version, role, license, how it gets there)
TOOLS = [
    ("OpenTofu", "1.12.6", "Creates the VMs (libvirt and Hyper-V backends)", "MPL-2.0", "Downloaded in the image build; SHA-256 checked (`TOFU_VERSION`)."),
    ("Packer", "1.16.1", "Builds VM templates", "BUSL-1.1", "Downloaded in the image build; SHA-256 checked (`PACKER_VERSION`). Business Source License: read its terms before offering LABaPe as a service."),
    ("step CLI", "0.30.2", "Talks to the platform CA", "Apache-2.0", "Downloaded in the image build; SHA-256 pinned for amd64 and arm64."),
    ("ansible-core", "2.21.4", "Configures VMs", "GPL-3.0-or-later", "pip, in the image build (`ANSIBLE_CORE_VERSION`)."),
]
COLLECTIONS = [  # (name, constraint, license, role)
    ("ansible.windows", "==3.8.0", "GPL-3.0", "Windows modules (win_package, win_user, ...)"),
    ("microsoft.ad", "==1.12.1", "GPL-3.0", "Active Directory modules"),
    ("community.general", "==13.4.0", "GPL-3.0", "General modules (npm, ...)"),
    ("chocolatey.chocolatey", "==1.6.0", "GPL-3.0", "Chocolatey packages on Windows"),
    ("community.library_inventory_filtering_v1", "(dependency of community.general)", "GPL-3.0", "Inventory filtering"),
]
PROVIDERS = [  # (source, constraint, installed, license, where)
    ("dmacvicar/libvirt", "~> 0.8", "0.9.9", "Apache-2.0", "tofu/backends/libvirt (mirrored into the image)"),
    ("hashicorp/local", "~> 2.5", "2.9.1", "MPL-2.0", "tofu/backends/libvirt (mirrored into the image)"),
    ("hashicorp/null", "~> 3.2", "3.3.2", "MPL-2.0", "tofu/backends/libvirt (mirrored into the image)"),
    ("taliesins/hyperv", "~> 1.2", "(not in the image)", "MPL-2.0", "tofu/backends/hyperv; fetched when a Hyper-V host deploys"),
]
PLUGINS = [("hashicorp/qemu", ">= 1.1.0", "1.1.7", "MPL-2.0"), ("hashicorp/ansible", ">= 1.1.0", "1.1.6", "MPL-2.0")]
# What LABaPe puts on VMs (roles and the catalog); (software, how, source, notes)
GUEST = [
    ("virtio-win guest tools", "0.1.302-1", "fedorapeople.org (Windows guests on KVM)", "SHA-256 pinned in ansible/roles/windows_common/defaults/main.yml; includes the QEMU guest agent driver."),
    ("Chocolatey packages", "latest", "community.chocolatey.org", "Whatever the catalog names; Node.js (LTS) and Git for Claude Code."),
    ("Mailpit", "latest release", "github.com/axllent/mailpit", "Catalog service Mail server (MIT). Downloaded when deployed, unpinned."),
    ("xrdp, TigerVNC, XFCE", "distribution packages", "the distribution's repositories (EPEL on the RHEL family)", "Desktop sessions on Linux groups that ask for them."),
    ("Node.js and npm", "distribution (22 stream on the RHEL family)", "distribution repositories", "For npm applications (Claude Code)."),
    ("@anthropic-ai/claude-code", "latest", "npm registry", "Catalog application, only when chosen; updated weekly on the VM."),
    ("Google Chrome", "stable", "dl.google.com repositories", "Catalog application (Linux); Windows from Catalog → Updates."),
    ("Microsoft packages (.NET, VS Code)", "distribution/vendor", "packages.microsoft.com", "Catalog applications."),
    ("Catalog → Updates downloads", "newest, N kept", "Microsoft's .NET feed, GitHub releases, vendor links, Chocolatey", ".NET 8 and 10, Chrome, KeePassXC, PuTTY; hashes checked where published."),
]
UPDATE_SOURCES = ["dotnet-8/10 runtime, hosting bundle, SDK (Windows)", "chrome-win", "keepassxc-win", "putty-win"]


def spdx(lic: str):
    lic = LICENSE_ALIASES.get(lic, lic)
    if lic in SPDX:
        return {"license": {"id": lic}}
    parts = re.split(r"\s+(?:OR|AND)\s+", lic)
    if len(parts) > 1 and all(p in SPDX for p in parts):
        return {"expression": lic}
    return {"license": {"name": lic}}


def norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def purl_npm(name: str, version: str) -> str:
    return f"pkg:npm/{quote(name, safe='')}@{version}" if not name.startswith("@") else f"pkg:npm/%40{quote(name[1:], safe='/')}@{version}"


def load_npm():
    lock = json.loads((ROOT / "web" / "package-lock.json").read_text(encoding="utf-8"))
    pk = lock["packages"]
    root = pk[""]
    direct = {**root.get("dependencies", {}), **root.get("devDependencies", {})}
    items = {}
    declared_dev = set(root.get("devDependencies", {}))
    for key, v in pk.items():
        if not key:
            continue
        name = key.rsplit("node_modules/", 1)[1]
        items[key] = {"key": key, "name": name, "version": v["version"], "license": v.get("license") or "unknown",
                      "dev": bool(v.get("dev")) or (key == f"node_modules/{name}" and name in declared_dev), "optional": bool(v.get("optional")), "integrity": v.get("integrity", ""),
                      "deps": {**v.get("dependencies", {}), **v.get("optionalDependencies", {})}}
    return items, direct, root


def resolve(items, key, dep):
    k = key
    while True:
        cand = (k + "/node_modules/" if k else "node_modules/") + dep
        if cand in items:
            return cand
        if not k:
            return None
        k = k.rsplit("/node_modules/", 1)[0] if "/node_modules/" in k else ""


def tools_with_versions(snap):
    """TOOLS with the versions the image really has (ansible-core's is the Python package's)."""
    have = snap.get("tools", {})
    py_core = next((p["version"] for p in snap["python"] if norm(p["name"]) == "ansible-core"), None)
    return [(n, py_core if n == "ansible-core" and py_core else have.get(n, v), r, l, h) for n, v, r, l, h in TOOLS]


def providers_with_versions(snap):
    have = snap.get("providers", {})
    return [(s, c, have.get(s, i), l, w) for s, c, i, l, w in PROVIDERS]


def plugins_with_versions(snap):
    have = snap.get("plugins", {})
    return [(s, c, have.get(s, i), l) for s, c, i, l in PLUGINS]


def steps_lists():
    tree = ast.parse((ROOT / "deploy" / "labape_deploy" / "steps.py").read_text(encoding="utf-8"))
    out = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                and n.targets[0].id in ("APT_PACKAGES", "WEB_PACKAGES", "RPM_PACKAGES"):
            out[n.targets[0].id] = ast.literal_eval(n.value)
    return out


def build():
    snap = json.loads(SNAP.read_text(encoding="utf-8"))
    npm, direct, _root = load_npm()
    pyproject = (ROOT / "app" / "pyproject.toml").read_text(encoding="utf-8")
    version = re.search(r'^version = "([^"]+)"', pyproject, re.M).group(1)
    declared = dict(re.findall(r'^\s+"([A-Za-z0-9_.\-]+)(?:\[[a-z]+\])?([<>=!~][^"]*)"', pyproject, re.M))
    py = {norm(p["name"]): p for p in snap["python"] if norm(p["name"]) != "labape"}
    comps = []   # CycloneDX components
    deps = {}    # bom-ref -> [bom-ref]
    root_ref = f"labape@{version}"

    def comp(ref, ctype, name, ver, purl, lic="", scope="required", props=None, hashes=None, desc=""):
        c = {"bom-ref": ref, "type": ctype, "name": name, "version": ver}
        if purl:
            c["purl"] = purl
        if desc:
            c["description"] = desc
        if lic and lic != "unknown":
            c["licenses"] = [spdx(l) for l in [lic]]
        c["scope"] = scope
        if hashes:
            c["hashes"] = hashes
        if props:
            c["properties"] = [{"name": k, "value": v} for k, v in props.items()]
        comps.append(c)

    # Python
    for n, p in sorted(py.items()):
        deb = p["origin"] == "debian"
        comp(f"pypi:{n}@{p['version']}", "library", p["name"], p["version"],
             f"pkg:pypi/{n}@{p['version']}", p["license"], props={"labape:group": "python (worker image)",
                                                                  "labape:installed-by": "Debian package" if deb else "pip",
                                                                  "labape:direct": str(n in {norm(k) for k in declared} or n in ("ansible-core", "pywinrm")).lower()})
        deps[f"pypi:{n}@{p['version']}"] = sorted(
            f"pypi:{norm(m.group(0))}@{py[norm(m.group(0))]['version']}" for r in p["requires"]
            if (m := re.match(r"[A-Za-z0-9_.\-]+", r)) and norm(m.group(0)) in py)
    # npm
    refs = {}
    for key, it in sorted(npm.items(), key=lambda kv: (kv[1]["name"], kv[1]["version"], kv[0])):
        ref = f"npm:{it['name']}@{it['version']}"
        refs[key] = ref
    for key, it in sorted(npm.items(), key=lambda kv: (kv[1]["name"], kv[1]["version"], kv[0])):
        if refs[key] in deps:
            continue
        hashes = []
        m = re.match(r"sha512-(.+)", it["integrity"])
        if m:
            import base64
            hashes = [{"alg": "SHA-512", "content": base64.b64decode(m.group(1)).hex()}]
        top = it["name"] in direct and key == f"node_modules/{it['name']}"
        comp(refs[key], "library", it["name"], it["version"], purl_npm(it["name"], it["version"]), it["license"],
             scope="optional" if it["dev"] else "required", hashes=hashes,
             props={"labape:group": "web (npm)", "labape:build-only": str(it["dev"]).lower(), "labape:direct": str(top).lower()})
        deps[refs[key]] = sorted({refs[r] for d in it["deps"] if (r := resolve(npm, key, d))})
    # Debian packages (worker image)
    for line in snap["apt"]:
        name, ver, arch = (line.split("\t") + ["?", "?"])[:3]
        comp(f"deb:{name}@{ver}", "library", name, ver, f"pkg:deb/debian/{name}@{quote(ver, safe='')}?arch={arch}&distro=debian-13",
             props={"labape:group": "Debian packages (worker image)"})
    for line in snap["syslog_image"]["packages"]:
        name, ver = line.split("\t")
        comp(f"deb-syslog:{name}@{ver}", "library", name, ver, f"pkg:deb/debian/{name}@{quote(ver, safe='')}?arch=amd64&distro=debian-13",
             props={"labape:group": "Debian packages (syslog image)"})
    # engine tools, collections, providers, plugins
    for name, ver, role, lic, how in tools_with_versions(snap):
        if name != "ansible-core":
            comp(f"tool:{name}@{ver}", "application", name, ver, f"pkg:generic/{quote(name.lower().replace(' ', '-'))}@{ver}", lic,
                 desc=role, props={"labape:group": "engine tools", "labape:source": how})
    for name, constraint, lic, role in COLLECTIONS:
        ver = snap["collections"][name]
        comp(f"collection:{name}@{ver}", "library", name, ver, f"pkg:generic/{name}@{ver}", lic, desc=role,
             props={"labape:group": "Ansible collections"})
    for src, constraint, installed, lic, where in providers_with_versions(snap):
        if installed[0].isdigit():
            comp(f"provider:{src}@{installed}", "library", src, installed, f"pkg:generic/opentofu-provider-{src.replace('/', '-')}@{installed}",
                 lic, props={"labape:group": "OpenTofu providers", "labape:constraint": constraint})
    for src, constraint, installed, lic in plugins_with_versions(snap):
        comp(f"plugin:{src}@{installed}", "library", src, installed, f"pkg:generic/packer-plugin-{src.replace('/', '-')}@{installed}", lic,
             props={"labape:group": "Packer plugins", "labape:constraint": constraint})
    # container images
    for image, role, lic, where, notes in IMAGES:
        digest = snap["images"].get(image, "")
        name, _, tag = image.rpartition(":")
        ref = f"image:{image}"
        purl = f"pkg:oci/{name.split('/')[-1]}{'@' + digest if digest else ''}?repository_url={quote(name if '/' in name else 'docker.io/library/' + name, safe='/.')}&tag={tag}"
        comp(ref, "container", image, tag, purl, lic if lic in SPDX else "", desc=role,
             props={"labape:group": "container images", "labape:declared-in": where, **({"labape:digest": digest} if digest else {})})
    root = {"bom-ref": root_ref, "type": "application", "name": "LABaPe", "version": version,
            "licenses": [{"license": {"id": "AGPL-3.0-only"}}],
            "description": "LABaPe: web interface, API and worker for building lab environments (KVM, Hyper-V), with the stack around them"}
    direct_refs = sorted(
        [f"pypi:{norm(k)}@{py[norm(k)]['version']}" for k in list(declared) + ["ansible-core", "pywinrm"] if norm(k) in py]
        + [refs[f"node_modules/{n}"] for n in direct if f"node_modules/{n}" in refs])
    for c in comps:
        deps.setdefault(c["bom-ref"], [])           # a leaf is listed too, with nothing under it
    dependencies = [{"ref": root_ref, "dependsOn": sorted(set(direct_refs))}] + [
        {"ref": r, "dependsOn": d} for r, d in sorted(deps.items())]
    serial = uuid.uuid5(uuid.NAMESPACE_URL, f"labape-sbom-{version}-{snap['captured']}")
    bom = {"bomFormat": "CycloneDX", "specVersion": "1.5", "serialNumber": f"urn:uuid:{serial}", "version": 1,
           "metadata": {"timestamp": f"{snap['captured']}T00:00:00Z",
                        "tools": {"components": [{"type": "application", "name": "tools/helpers/sbom.py"}]},
                        "component": root,
                        "properties": [{"name": "labape:scope", "value": "the repository at this version and the lab stack it was captured from"}]},
           "components": comps, "dependencies": dependencies}
    return bom, snap, npm, direct, py, declared, version


def md_table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return "\n".join(out)


def document(bom, snap, npm, direct, py, declared, version):
    steps = steps_lists()
    runtime = [i for i in npm.values() if not i["dev"]]
    devs = [i for i in npm.values() if i["dev"]]
    lic_npm = {}
    for i in npm.values():
        lic_npm[i["license"]] = lic_npm.get(i["license"], 0) + 1
    deb_py = [p for p in py.values() if p["origin"] == "debian"]
    parts = [f"""# Software bill of materials

> **Stage: Reference.** What LABaPe {version} is made of and installs: every package, tool and image, with versions,
> licenses and what each is for. Machine-readable twin: [`sbom/labape.cdx.json`](../sbom/labape.cdx.json)
> (CycloneDX 1.5, with package URLs, hashes for npm packages and the dependency graph).
> Generated by `tools/helpers/sbom.py`; regenerate after changing a dependency (`--check` says whether it is stale).

## 1. Scope, sources and limits

| | |
|---|---|
| Version | LABaPe {version} (`app/pyproject.toml`, `web/package.json`) |
| Captured | {snap['captured']}, from {snap['captured_from']}. A release captures it from a clean `main` (`tools/helpers/release.sh`) |
| Python | **Exact versions come from the built image**: `app/pyproject.toml` declares only lower bounds, and the repository has no lock file for Python, so a rebuild can install newer ones. {len(py)} packages ({len(deb_py)} from Debian, {len(py) - len(deb_py)} from pip) |
| Web (npm) | `web/package-lock.json`: {len(npm)} packages, every version and integrity hash ({len(runtime)} are bundled into the UI, {len(devs)} are build tooling only; the image carries just the built files) |
| Images, tools | Pins from `container/Dockerfile`, `container/compose.yaml` and `tools/authentik-test/compose.yaml`; image digests as pulled on the lab |
| Licenses | As the packages and projects declare them (Python and npm metadata; the images' upstream projects). **Debian packages' licenses aren't recorded here**: each package's is in `/usr/share/doc/<package>/copyright`. Not a legal review, and **not a vulnerability scan**: feed the CycloneDX file to a scanner (Grype, Trivy, OSV-Scanner) for that |
| LABaPe's own license | AGPL-3.0-only (`LICENSE`; `app/pyproject.toml` and `web/package.json` say so too) |
| Not covered | Operating-system ISOs and VM templates you supply (Windows Server, Rocky, Debian); what runs *inside* an environment's VMs beyond section 9; the lab hosts' own operating systems |

Summary: {len(py)} Python, {len(npm)} npm, {len(snap['apt']) + len(snap['syslog_image']['packages'])} explicitly installed Debian packages
(of {snap['dpkg_total']} in the worker image and {snap['syslog_image']['dpkg_total']} in the syslog image), {len(IMAGES)} third-party images,
{len(TOOLS)} engine tools, {len(COLLECTIONS)} Ansible collections, {len(PROVIDERS)} OpenTofu providers, {len(PLUGINS)} Packer plugins.
CycloneDX components: {len(bom['components'])}.

## 2. How the pieces fit

```
browser ──HTTPS──▶ Caddy ──▶ LABaPe API / worker image (Python, built UI)
                              │   ├─ OpenTofu + libvirt provider ─▶ KVM hosts (VMs)
                              │   ├─ Packer + qemu plugin        ─▶ templates
                              │   ├─ Ansible + collections       ─▶ Windows (WinRM), Linux (SSH)
                              │   └─ step CLI ─▶ step-ca (platform CA)
                              ├─ PostgreSQL ── LABaPe's data     ├─ OpenBao (+ Conjur) ── secrets
                              └─ rsyslog + PostgreSQL ── logs     └─ guacd ── browser RDP/SSH/VNC
```

One image (`labape:local`) runs both the API and the worker; it holds the Python application, the built UI and the
engine's tools. Everything else is a separate container from a public image (section 5).

## 3. Python (the worker image)

Direct dependencies, as declared in `app/pyproject.toml`, and the two the image adds for the engine:
"""]
    rows = []
    for k in list(declared) + ["ansible-core", "pywinrm"]:
        n = norm(k)
        if n in py:
            p = py[n]
            rows.append((p["name"], declared.get(k, "").strip() or ("==" + p["version"] if k == "ansible-core" else "(unpinned)"),
                         p["version"], p["license"], PY_PURPOSE.get(p["name"]) or PY_PURPOSE.get(k) or p["summary"]))
    parts.append(md_table(["Package", "Declared", "Installed", "License", "What it is for"], rows))
    parts.append("\n\nAll installed packages, direct and transitive "
                 "(`origin`: **pip** = installed by the image build, **deb** = a Debian package the venv sees through `--system-site-packages`):\n")
    parts.append("<details><summary>" + f"{len(py)} Python packages" + "</summary>\n")
    parts.append(md_table(["Package", "Version", "License", "Origin", "Summary"],
                          [(p["name"], p["version"], p["license"], "deb" if p["origin"] == "debian" else "pip", p["summary"])
                           for _, p in sorted(py.items())]))
    parts.append("\n</details>\n")
    parts.append("""
Notes: `pytest` is needed to run the tests but isn't declared anywhere (no dev extras in `pyproject.toml`).
The libraries from Debian (`requests`, `urllib3`, `certifi`, ...) are older than pip would give; they reach the
venv because it is created with `--system-site-packages` (for `python3-libvirt`).

## 4. Web interface (npm)

Built in the image's `web` stage (`npm ci` from the lock file, then `tsc` and `vite build`); the final image has only the bundle.
""")
    drow = []
    for n, rng in sorted({**load_npm()[1]}.items()):
        it = npm[f"node_modules/{n}"]
        drow.append((n, rng, it["version"], it["license"], "build only" if it["dev"] else "runtime", WEB_PURPOSE.get(n, "")))
    parts.append(md_table(["Package", "Declared", "Locked", "License", "Role", "What it is for"], drow))
    parts.append("\n\nLicenses across all %d locked packages: %s.\n" % (
        len(npm), ", ".join(f"{k} {v}" for k, v in sorted(lic_npm.items(), key=lambda x: -x[1]))))
    parts.append("<details><summary>" + f"All {len(npm)} locked npm packages" + "</summary>\n")
    parts.append(md_table(["Package", "Version", "License", "Role"],
                          [(i["name"], i["version"], i["license"], "build only" if i["dev"] else "runtime")
                           for i in sorted(npm.values(), key=lambda i: (i["name"], i["version"]))]))
    parts.append("\n</details>\n\n## 5. Container images\n\n")
    img_rows = []
    for image, role, lic, where, notes in IMAGES:
        img_rows.append((f"`{image}`", snap["images"].get(image, "(tag only)").replace("sha256:", "sha256:") if image in snap["images"] else "(tag only)",
                         role, lic, where, notes))
    parts.append(md_table(["Image", "Digest (lab)", "Role", "License", "Declared in", "Notes"], img_rows))
    parts.append("""

The two LABaPe images (`labape:local`, `labape-syslog:local`) are built from this repository by `docker compose build`.
Licenses of third-party images are the upstream projects', not checked against the image layers.

## 6. Engine tools in the worker image

""")
    parts.append(md_table(["Tool", "Version", "What it does", "License", "How it gets there"], tools_with_versions(snap)))
    parts.append("\n\nAnsible collections (`ansible-galaxy collection install`, pinned, three tries in the build):\n\n")
    parts.append(md_table(["Collection", "Constraint", "Installed", "License", "What for"],
                          [(n, c, snap["collections"][n], l, r) for n, c, l, r in COLLECTIONS]))
    parts.append("\n\nOpenTofu providers (mirrored into the image at build time, so deploys don't reach the internet for them):\n\n")
    parts.append(md_table(["Provider", "Constraint", "Installed", "License", "Where used"], providers_with_versions(snap)))
    parts.append("\n\nPacker plugins (`packer plugins install`, three tries):\n\n")
    parts.append(md_table(["Plugin", "Constraint", "Installed", "License"], plugins_with_versions(snap)))
    parts.append(f"""

## 7. Debian packages

**Worker image** (Debian {snap['os'].split('(')[0].replace('Debian GNU/Linux', '').strip()} trixie), installed explicitly by the Dockerfile; {snap['dpkg_total']} packages in all with their dependencies:

""")
    parts.append(md_table(["Package", "Version", "Architecture"], [tuple((l.split("\t") + ["", ""])[:3]) for l in snap["apt"]]))
    parts.append(f"\n\n**Syslog image** ({snap['syslog_image']['dpkg_total']} packages in all):\n\n")
    parts.append(md_table(["Package", "Version"], [tuple(l.split("\t")) for l in snap["syslog_image"]["packages"]]))
    parts.append(f"""

## 8. Hosts the installer prepares

`deploy/install-labape.py` installs these from the host's own repositories (unpinned: whatever the distribution ships),
plus tools downloaded with hashes checked. It reads `deploy/labape_deploy/steps.py`, which this table follows.

""")
    parts.append(md_table(["Profile", "Packages"], [
        ("KVM host, Debian family (`APT_PACKAGES`)", ", ".join(steps["APT_PACKAGES"])),
        ("Web-only stack host, Debian family (`WEB_PACKAGES`)", ", ".join(steps["WEB_PACKAGES"])),
        ("KVM host, RHEL family (`RPM_PACKAGES`)", ", ".join(steps["RPM_PACKAGES"]) + " (and python3.12, python3.12-pip on RHEL 9)"),
    ]))
    parts.append("""

Downloaded by the installer on a KVM host: OpenTofu and Packer (versions as in the Dockerfile, SHA-256 checked),
the Packer plugins above, and `ansible-core` plus `pywinrm` and `pyyaml` into a venv (`ansible-core` 2.21.4, or
`>=2.19,<2.20` on Debian 12 hosts whose Python is too old). Container hosts also need Docker or Podman with Compose;
the installer doesn't pin them.

## 9. What LABaPe installs on the VMs it builds

""")
    parts.append(md_table(["Software", "Version", "Comes from", "Notes"], GUEST))
    parts.append("""

Catalog → Updates keeps the newest few downloads of: """ + ", ".join(UPDATE_SOURCES) + """ in Files → Installers.
Admins can add sources of their own, so this list grows with use; the Updates tab is the current record.

## 10. Gaps and things to know

1. **Python isn't locked.** Add a constraints file (`pip freeze` of a good build, or `uv lock`) and install with it, so builds repeat and this SBOM can come from the repository alone.
2. **Packer's Business Source License (BUSL-1.1)** applies from 1.10 on; OpenTofu is the open-source fork of Terraform for the same reason. Fine for internal use; check before offering LABaPe to others as a service.
3. **Floating tags**: `caddy:2`, `debian:trixie-slim` and `node:22-bookworm-slim` move; the digests above are what the lab pulled on the capture date. Pin by digest for repeatable builds.
4. **Unpinned downloads on VMs**: Mailpit's latest release, `claude-code`, Chocolatey and distribution packages are whatever is current at deploy time.
5. **Copyleft components.** `ansible-core` and its collections (GPL-3.0) run as separate processes, not linked into LABaPe. `psycopg` (LGPL-3.0) and `libvirt-python`/`PyGObject` (LGPL) are imported as libraries: fine to use unmodified, but a modified copy would need its source offered. Conjur Open Source is LGPL-3.0.
6. **No vulnerability data here.** Run a scanner on `sbom/labape.cdx.json` regularly; the lab's Authentik, OpenBao and Conjur images are the most exposed, as they face the network.
""")
    return "\n".join(parts) + "\n"


def main() -> int:
    bom, snap, npm, direct, py, declared, version = build()
    md = document(bom, snap, npm, direct, py, declared, version)
    cdx = json.dumps(bom, indent=1, ensure_ascii=False) + "\n"
    if "--check" in sys.argv:
        stale = [p.name for p, t in ((MD, md), (CDX, cdx)) if not p.exists() or p.read_text(encoding="utf-8") != t]
        print("stale: " + ", ".join(stale) if stale else "up to date")
        return 1 if stale else 0
    CDX.parent.mkdir(exist_ok=True)
    MD.write_text(md, encoding="utf-8", newline="\n")
    CDX.write_text(cdx, encoding="utf-8", newline="\n")
    print(f"wrote {MD.relative_to(ROOT)} and {CDX.relative_to(ROOT)}: {len(bom['components'])} components")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
