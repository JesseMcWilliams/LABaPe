"""Every question the installer can ask: name, prompt, default, validation.

An answer comes from (highest first) --set NAME=VALUE, --config-file, the
saved answers file (on --resume/--step/--start-at), or a prompt. Only the
answers the selected steps need are asked for. Passwords and secrets are
never answers: the OIDC client secret is prompted and written straight to
container/secrets/, and vault passwords are generated into the vault.
"""
from __future__ import annotations

import getpass
import ipaddress
import os
import socket
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Answer:
    name: str
    prompt: str
    kind: str = "text"                    # text | bool | choice | list | networks
    default: Any = None                   # value or callable(answers) -> value
    choices: tuple = ()
    help: str = ""
    validate: Callable[[Any], str | None] | None = None   # returns an error message
    ask: bool = True                      # False: never prompted, the default is used


def _primary_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return ""


def _cidr(v):
    try:
        ipaddress.IPv4Network(str(v), strict=False)
    except ValueError as exc:
        return str(exc)
    return None


ANSWERS: dict[str, Answer] = {a.name: a for a in [
    Answer("InstanceName", "Name for this installation (state files are kept per name)", default="labape", ask=False),
    Answer("ServiceUser", "Linux account that owns the repo, vault and SSH keys and runs the CLI",
           default=lambda a: os.environ.get("SUDO_USER") or "labape"),
    Answer("InstallCli", "Install the CLI toolchain on this host (OpenTofu, Packer)?", kind="bool", default=True),
    Answer("InstallWebUi", "Install the web interface (container stack)?", kind="bool", default=True),
    Answer("KvmHostName", "Name of this KVM host in the web interface", default=lambda a: socket.gethostname().split(".")[0]),
    Answer("VmStoragePath", "Directory for VM disks", default="/data/VMs/LABaPe"),
    Answer("TemplateStoragePath", "Directory for the template library",
           default=lambda a: a.get("VmStoragePath", "/data/VMs/LABaPe").rstrip("/") + "/templates"),
    Answer("IsoPath", "Directory holding OS installation ISOs", default="/data/OS_Images"),
    Answer("DomainName", "Active Directory domain for lab environments", default="labape.test"),
    Answer("NetbiosName", "NetBIOS name of that domain", default=lambda a: a.get("DomainName", "labape").split(".")[0].upper()[:15]),
    Answer("ManagementSource", "IP or CIDR that manages the lab VMs (SSH firewall scoping)", default=lambda a: _primary_ip()),
    Answer("Networks", "VM networks", kind="networks", default=lambda a: [],
           help="Each VM network: a name, the host bridge (created on a NIC if needed), its addressing rules."),
    Answer("DefaultNetwork", "Default network for host groups that don't name one",
           default=lambda a: (a.get("Networks") or [{}])[0].get("Name", "")),
    Answer("WebHostname", "Hostname (or IP) browsers use for the web interface", default=lambda a: _primary_ip()),
    Answer("Tls", "HTTPS certificate: 'internal' (Caddy's own CA) or an email address for ACME", default="internal"),
    Answer("ContainerRuntime", "Container runtime", kind="choice", choices=("docker", "podman"), default="docker"),
    Answer("AuthMode", "Sign-in: an existing OIDC provider (Authentik), a throwaway test Authentik, or none (break-glass only)",
           kind="choice", choices=("existing", "test-authentik", "none"), default="existing"),
    Answer("OidcIssuer", "OIDC issuer URL (e.g. https://authentik.example/application/o/labape/)"),
    Answer("OidcClientId", "OIDC client ID", default="labape"),
    Answer("AdminGroups", "Authentik groups that get the admin role, besides labape-admins", kind="list", default=lambda a: []),
    Answer("TemplatesToBuild", "Templates to build now, as os-key:name (e.g. rocky9:rocky9-base-2026.10); empty for none",
           kind="list", default=lambda a: []),
    Answer("TofuVersion", "OpenTofu version", default="1.12.6", ask=False),
    Answer("PackerVersion", "Packer version", default="1.16.1", ask=False),
    Answer("AnsibleCoreVersion", "ansible-core version", default="2.21.4", ask=False),
]}

for _a in ("OidcIssuer",):
    ANSWERS[_a].validate = lambda v: None if str(v).startswith(("http://", "https://")) else "must be an http(s) URL"


@dataclass
class Resolver:
    """Holds the answers for one run and prompts for missing ones."""

    answers: dict = field(default_factory=dict)
    answered: set = field(default_factory=set)
    interactive: bool = True

    def add(self, source: dict, overwrite: bool = False) -> None:
        for k, v in source.items():
            if overwrite or k not in self.answered:
                self.answers[k] = v
                self.answered.add(k)

    def get(self, name: str):
        return self.need(name)

    def need(self, name: str):
        """The answer, prompting (or defaulting) if it isn't set yet."""
        if name in self.answered:
            return self.answers[name]
        a = ANSWERS[name]
        default = a.default(self.answers) if callable(a.default) else a.default
        if not a.ask or not self.interactive:
            if default in (None, "") and a.ask:
                raise SystemExit(f"error: {name} is required ({a.prompt}); pass it with --set {name}=... "
                                 f"or in --config-file (not prompting: no terminal).")
            value = default
        else:
            value = self._prompt(a, default)
        self.answers[name] = value
        self.answered.add(name)
        return value

    def _prompt(self, a: Answer, default):
        if a.help:
            print(f"\n{a.help}")
        while True:
            if a.kind == "bool":
                d = "Y/n" if default else "y/N"
                raw = input(f"{a.prompt} ({d}) ").strip().lower()
                value = default if raw == "" else raw.startswith("y")
            elif a.kind == "choice":
                raw = input(f"{a.prompt} [{'/'.join(a.choices)}] ({default}) ").strip()
                value = raw or default
                if value not in a.choices:
                    print(f"  must be one of: {', '.join(a.choices)}")
                    continue
            elif a.kind == "list":
                raw = input(f"{a.prompt} (comma-separated) [{', '.join(default or []) or 'none'}] ").strip()
                value = [x.strip() for x in raw.split(",") if x.strip()] if raw else list(default or [])
            elif a.kind == "networks":
                value = prompt_networks()
            else:
                shown = f" [{default}]" if default not in (None, "") else ""
                raw = input(f"{a.prompt}{shown}: ").strip()
                value = raw or default
                if value in (None, ""):
                    print("  this value is required")
                    continue
            err = a.validate(value) if a.validate else None
            if err:
                print(f"  {err}")
                continue
            return value


def _ask(prompt: str, default: str = "", validate=None, required: bool = True) -> str:
    while True:
        shown = f" [{default}]" if default else ""
        v = input(f"  {prompt}{shown}: ").strip() or default
        if not v and not required:
            return ""
        if not v:
            print("    required")
            continue
        err = validate(v) if validate else None
        if err:
            print(f"    {err}")
            continue
        return v


def _csv(prompt: str, default: str = "") -> list[str]:
    raw = input(f"  {prompt}{f' [{default}]' if default else ''}: ").strip() or default
    return [x.strip() for x in raw.split(",") if x.strip()]


def prompt_networks() -> list[dict]:
    """Interactive entry of the VM network catalog."""
    nets: list[dict] = []
    while True:
        more = "another" if nets else "a"
        if input(f"Add {more} VM network? ({'y/N' if nets else 'Y/n'}) ").strip().lower() not in (
                ("y", "yes") if nets else ("", "y", "yes")):
            if nets:
                return nets
            print("  at least one VM network is needed")
            continue
        name = _ask("Network name (e.g. lab-vlan48)")
        cidr = _ask("Network CIDR (e.g. 172.21.48.0/22)", validate=_cidr)
        net = ipaddress.IPv4Network(cidr, strict=False)
        gateway = _ask("Gateway", str(net.network_address + 1))
        dns = _csv("DNS servers", gateway)
        bridge = _ask("Host bridge for it (e.g. br1)")
        nic = _ask("Physical NIC to build the bridge on, if it doesn't exist yet (empty: the bridge exists)",
                   required=False)
        host_addr = "dhcp"
        if nic:
            host_addr = _ask("Host address on the bridge: dhcp (needed to find DHCP VMs) or none", "dhcp",
                             validate=lambda v: None if v in ("dhcp", "none") else "dhcp or none")
        modes = _csv("Allowed addressing (static, dhcp)", "static,dhcp")
        pools = _csv("Static pools LABaPe may assign (a-b, ...)") if "static" in modes else []
        dhcp = _csv("The DHCP server's scope (a-b, ...)") if "dhcp" in modes else []
        reserved = _csv("Reserved ranges, never assigned (a-b, ...; empty for none)")
        groups = _csv("Limit to these groups (empty: anyone who deploys)")
        nets.append({"Name": name, "Cidr": cidr, "Gateway": gateway, "DnsServers": dns, "Bridge": bridge,
                     "Nic": nic, "HostAddress": host_addr, "Addressing": modes, "StaticPools": pools,
                     "DhcpRanges": dhcp, "Reserved": reserved, "AllowedGroups": groups})
