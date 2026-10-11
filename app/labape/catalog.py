"""Service, application and package catalog (design §21.4, questions 51-53).

One table, `catalog_items`, holds three kinds:

- application: how to install a piece of software on each platform, in the
  shape of ansible/package_catalog.yml entries (Claude_Docs/Design_Software-Manifest.md):
  per-package-manager names (`chocolatey`, `apt`, `dnf`, `zypper`), and/or
  `windows` / `linux` custom installers ({type: msi|exe|deb|rpm, source:
  url|local, url | path, arguments, product_id, ...}). A local `path` is a
  file in the Files tab's installers location.
- service: what to enable on top of the template: {windows: {features:
  [...], services: [...]}, linux: {packages: {apt: [...], dnf: [...]},
  units: [...]}}.
- package: a named group: {items: [{kind, name}]}, where a package may
  include packages one level deep (question 53).

Only published items can be chosen for a host group. Users request new
items; admins create, import, publish and retire them (questions 51, 52).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CatalogItem

KINDS = ("service", "application", "package")
PKG_MANAGERS = ("chocolatey", "apt", "dnf", "zypper")
INSTALLER_TYPES = {"windows": ("msi", "exe"), "linux": ("deb", "rpm")}


class CatalogError(ValueError):
    def __init__(self, message: str, field: str = "definition"):
        super().__init__(message)
        self.field = field


def platforms(kind: str, definition: dict) -> list[str]:
    d = definition or {}
    out = set()
    if kind == "application":
        if "chocolatey" in d or "windows" in d:
            out.add("windows")
        if any(k in d for k in ("apt", "dnf", "zypper")) or "linux" in d:
            out.add("linux")
    elif kind == "service":
        out |= {p for p in ("windows", "linux") if p in d}
    return sorted(out)


def validate(db: Session, kind: str, definition: dict) -> None:
    d = definition or {}
    if kind == "application":
        unknown = [k for k in d if k not in PKG_MANAGERS + ("windows", "linux")]
        if unknown:
            raise CatalogError(f"Unknown keys {unknown}; use {', '.join(PKG_MANAGERS)}, windows or linux.")
        if not d:
            raise CatalogError("Give at least one install method (a package name or an installer).")
        for plat in ("windows", "linux"):
            if plat in d:
                inst = d[plat] or {}
                if inst.get("type") not in INSTALLER_TYPES[plat]:
                    raise CatalogError(f"{plat}.type must be one of {', '.join(INSTALLER_TYPES[plat])}.")
                if inst.get("source") not in ("url", "local"):
                    raise CatalogError(f"{plat}.source must be url or local.")
                if inst["source"] == "url" and not str(inst.get("url", "")).startswith(("http://", "https://")):
                    raise CatalogError(f"{plat}.url must be an http(s) URL.")
                if inst["source"] == "local" and not inst.get("path"):
                    raise CatalogError(f"{plat}.path must name a file in the installers location.")
                if plat == "windows" and inst.get("type") == "msi" and not inst.get("product_id"):
                    raise CatalogError("windows.product_id (the MSI's product code) is needed to tell if it's installed.")
    elif kind == "service":
        if not d or any(k not in ("windows", "linux") for k in d):
            raise CatalogError("A service defines windows and/or linux sections.")
        w, lx = d.get("windows") or {}, d.get("linux") or {}
        if "windows" in d and not (w.get("features") or w.get("services")):
            raise CatalogError("windows needs features (Windows feature names) and/or services.")
        if "linux" in d and not (lx.get("packages") or lx.get("units")):
            raise CatalogError("linux needs packages (per package manager) and/or units (systemd).")
        if lx.get("packages") and any(k not in PKG_MANAGERS for k in lx["packages"]):
            raise CatalogError(f"linux.packages keys must be package managers: {', '.join(PKG_MANAGERS[1:])}.")
    elif kind == "package":
        items = d.get("items") or []
        if not items:
            raise CatalogError("A package lists at least one item.", "items")
        for it in items:
            k, name = it.get("kind"), it.get("name")
            if k not in KINDS:
                raise CatalogError(f"Item {name!r}: kind must be service, application or package.", "items")
            ref = db.scalar(select(CatalogItem).where(CatalogItem.kind == k, CatalogItem.name == name))
            if ref is None:
                raise CatalogError(f"There's no {k} named {name!r} in the catalog.", "items")
            if k == "package" and any(x.get("kind") == "package" for x in (ref.definition or {}).get("items", [])):
                raise CatalogError(f"Package {name!r} already includes packages; packages nest one level only.",
                                   "items")
    else:
        raise CatalogError("kind must be service, application or package.", "kind")


def expand(db: Session, services: list[str], applications: list[str], packages: list[str]) -> dict:
    """A host group's choices, with packages expanded, as published items:
    {"services": {name: definition}, "applications": {name: definition}}."""
    pub = {(i.kind, i.name): i for i in db.scalars(select(CatalogItem).where(CatalogItem.status == "published"))}
    out = {"services": {}, "applications": {}}

    def add(kind: str, name: str, depth: int = 0) -> None:
        item = pub.get((kind, name))
        if item is None:
            raise CatalogError(f"{kind.capitalize()} {name!r} isn't published in the catalog.", f"{kind}s")
        if kind == "package":
            if depth > 1:
                return
            for it in (item.definition or {}).get("items", []):
                add(it["kind"], it["name"], depth + 1)
        else:
            out[f"{kind}s"][name] = item.definition
    for n in services:
        add("service", n)
    for n in applications:
        add("application", n)
    for n in packages:
        add("package", n)
    return out


# Starter catalog, published on first start (only when the catalog is empty).
SEED = [
    ("service", "iis", "IIS web server", "Internet Information Services with the management console.",
     {"windows": {"features": ["Web-Server", "Web-Mgmt-Console"], "services": ["W3SVC"]}}),
    ("service", "dns_server", "DNS server", "The Windows DNS Server role (not needed on domain controllers, which have it).",
     {"windows": {"features": ["DNS", "RSAT-DNS-Server"], "services": ["DNS"]}}),
    ("service", "dhcp_server", "DHCP server", "The Windows DHCP Server role. Configure scopes afterwards; don't "
     "authorize it on a LAN segment that already has DHCP.", {"windows": {"features": ["DHCP", "RSAT-DHCP"]}}),
    ("service", "file_server", "File server", "Windows file server role (SMB shares).",
     {"windows": {"features": ["FS-FileServer"]}}),
    ("service", "nginx", "nginx web server", "nginx, enabled and started.",
     {"linux": {"packages": {"dnf": ["nginx"], "apt": ["nginx"]}, "units": ["nginx"]}}),
    ("service", "podman", "Podman container runtime", "Podman and its compose tooling.",
     {"linux": {"packages": {"dnf": ["podman"], "apt": ["podman"]}}}),
    ("service", "cockpit", "Cockpit web console", "Browser-based server management on port 9090.",
     {"linux": {"packages": {"dnf": ["cockpit"], "apt": ["cockpit"]}, "units": ["cockpit.socket"]}}),
]


def seed(db: Session, package_catalog: str | None = None) -> int:
    """Publish the starter services, plus the applications in the engine's
    package_catalog.yml, when the catalog is empty (first start)."""
    if db.scalar(select(CatalogItem.id).limit(1)) is not None:
        return 0
    n = 0
    for kind, name, display, desc, definition in SEED:
        db.add(CatalogItem(kind=kind, name=name, display_name=display, description=desc, definition=definition,
                           platforms=platforms(kind, definition), status="published", created_by="seed",
                           updated_by="seed"))
        n += 1
    if package_catalog:
        import yaml
        try:
            entries = (yaml.safe_load(open(package_catalog, encoding="utf-8")) or {}).get("packages", {})
        except OSError:
            entries = {}
        for name, definition in entries.items():
            text = str(definition)
            if "example.com" in text or "{B1234567" in text or "{C7654321" in text:
                continue  # the catalog's illustrative placeholders
            try:
                validate(db, "application", definition)
            except CatalogError:
                continue
            db.add(CatalogItem(kind="application", name=name.replace("_", "-"), display_name=name.replace("_", " ").title(),
                               description="From the repository's package_catalog.yml.", definition=definition,
                               platforms=platforms("application", definition), status="published",
                               created_by="seed", updated_by="seed"))
            n += 1
    db.commit()
    return n
