"""Batch S4 (design §53.2): every stack container logs to the stack syslog collector."""
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy"))
if "pwd" not in sys.modules:                 # the installer runs on Linux; these tests also run on Windows
    try:
        import pwd  # noqa: F401
    except ImportError:
        import types
        sys.modules["pwd"] = types.ModuleType("pwd")
from labape_deploy import steps as S  # noqa: E402


class _Loader(yaml.SafeLoader):
    pass


def _tagged(loader, node):
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    return loader.construct_scalar(node)


for tag in ("!override", "!reset"):
    _Loader.add_constructor(tag, _tagged)


def _load(path):
    return yaml.load((ROOT / path).read_text(encoding="utf-8"), Loader=_Loader)


def test_every_service_but_the_collector_logs_to_syslog():
    compose = _load("container/compose.yaml")
    logged = {n for n, svc in compose["services"].items() if svc.get("logging", {}).get("driver") == "syslog"}
    assert logged == set(compose["services"]) - {"labape-syslog", "labape-syslog-db"}
    assert logged == set(S.LOGGED_SERVICES)
    opts = compose["services"]["caddy"]["logging"]["options"]
    assert opts["syslog-address"].startswith("udp://") and opts["syslog-address"].endswith(":514") and "{{.Name}}" in opts["tag"]


def test_the_authentik_stack_logs_to_syslog_too():
    compose = _load("tools/authentik-test/compose.yaml")
    assert compose["services"]["postgresql"]["logging"]["driver"] == "syslog"
    assert compose["x-authentik"]["logging"]["driver"] == "syslog"


def test_the_installer_logs_locally_where_there_is_no_syslog_driver_or_collector():
    assert S.services_override({}) is None                                   # Docker with the collector: the compose file's default
    podman = S.services_override({}, runtime="podman")
    assert "labape-api:\n    logging: !override\n      driver: journald\n" in podman and "labape-syslog" not in podman
    nocollector = S.services_override({"InstallSyslog": False})
    assert "driver: json-file" in nocollector and "labape-syslog:\n    profiles" in nocollector
    doc = yaml.load(S.services_override({"InstallConjur": False}, runtime="podman"), Loader=_Loader)
    assert set(doc["services"]["labape-conjur"]) == {"profiles", "logging"}  # one block per service, no duplicate keys
