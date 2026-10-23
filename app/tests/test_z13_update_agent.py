"""The update agent's restart_service and stores_setup actions (deploy/update-agent/labape-update.sh), run for real
against stub `docker`, `curl`, `git`, `sleep` and `stores.sh` commands: what it refuses, what it runs, what it writes
to status.json. (The update and rollback paths are tried on a lab host, not here.)"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "update-agent" / "labape-update.sh"


def _bash() -> str | None:
    candidates = [os.environ.get("LABAPE_TEST_BASH"), r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")]
    for c in candidates:
        if c and Path(c).exists() or (c and shutil.which(c)):
            try:
                if subprocess.run([c, "-c", "echo ok"], capture_output=True, text=True, timeout=20).stdout.strip() == "ok":
                    return c
            except (OSError, subprocess.TimeoutExpired):
                continue
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no working bash")


def _px(p: Path) -> str:
    return p.as_posix()


def _on_path(p: Path) -> str:
    """For a PATH entry: `C:/x` has a colon that bash would read as a separator, so Windows paths are written `/c/x`."""
    s = p.as_posix()
    return f"/{s[0].lower()}{s[2:]}" if len(s) > 2 and s[1] == ":" else s


@pytest.fixture
def agent(tmp_path):
    repo, home, stubs = tmp_path / "repo", tmp_path / "home", tmp_path / "stubs"
    (repo / "container" / "update").mkdir(parents=True)
    (repo / "container" / "stores").mkdir(parents=True)
    home.mkdir()
    stubs.mkdir()
    log = tmp_path / "calls.log"

    def stub(name: str, body: str) -> None:
        f = stubs / name
        f.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
        f.chmod(f.stat().st_mode | stat.S_IEXEC)

    stub("docker", f'echo "docker $*" >> "{_px(log)}"\n'
                   'case "$*" in\n'
                   '  *" restart "*) [ "${STUB_RESTART_RC:-0}" = 0 ] || exit "$STUB_RESTART_RC" ;;\n'
                   '  *" health --deep"*) exit "${STUB_HEALTH_RC:-0}" ;;\n'
                   'esac\nexit 0\n')
    stub("curl", f'echo "curl $*" >> "{_px(log)}"\necho \'{{"ok":true,"maintenance":false}}\'\n')
    stub("git", "exit 1\n")
    stub("sleep", "exit 0\n")
    stub("python3", f'exec "{_px(Path(sys.executable))}" "$@"\n')
    (repo / "container" / "stores" / "stores.sh").write_text(
        f'echo "stores.sh $*${{LABAPE_STORES_CLI_CHECK:+ [cli-check=$LABAPE_STORES_CLI_CHECK]}}" >> "{_px(log)}"\n'
        'exit "${STUB_STORES_RC:-0}"\n', encoding="utf-8", newline="\n")

    def run(action: str, target: str = "", **env) -> dict:
        update = repo / "container" / "update"
        for f in ("status.json", "processing.json", "request.json"):
            (update / f).unlink(missing_ok=True)
        log.write_text("", encoding="utf-8")
        (update / "request.json").write_text(json.dumps({"id": "t1", "action": action, "target": target, "requested_by": "tester"}),
                                             encoding="utf-8")
        e = {**os.environ, "HOME": _px(home), "LABAPE_REPO": _px(repo), "LABAPE_RESTART_HEALTH_SECONDS": "1", **env}
        # The stubs go first from inside bash: Git Bash puts its own /mingw64/bin ahead of the PATH it is given, so a real
        # curl would beat the stub.
        r = subprocess.run([BASH, "-c", f'export PATH="{_on_path(stubs)}:$PATH"; exec bash "{_px(SCRIPT)}"'], env=e,
                           capture_output=True, text=True, timeout=120)
        status = json.loads((update / "status.json").read_text(encoding="utf-8")) if (update / "status.json").exists() else None
        return {"rc": r.returncode, "status": status, "calls": [x for x in log.read_text(encoding="utf-8").splitlines() if x],
                "left_over": (update / "processing.json").exists() or (update / "request.json").exists(),
                "log": (update / "agent.log").read_text(encoding="utf-8") if (update / "agent.log").exists() else r.stderr}

    return run


def test_it_does_nothing_without_a_request(tmp_path, agent):
    # `agent` made the folders; with no request file the script exits at once and writes nothing
    r = subprocess.run([BASH, _px(SCRIPT)], env={**os.environ, "HOME": _px(tmp_path / "home"), "LABAPE_REPO": _px(tmp_path / "repo")},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0 and not (tmp_path / "repo" / "container" / "update" / "status.json").exists()


def test_an_unknown_service_is_refused_and_nothing_is_restarted(agent):
    r = agent("restart_service", "postgres")
    assert r["status"]["state"] == "failed" and r["status"]["step"] == "checking"
    assert "not a service the agent restarts" in r["status"]["message"]
    assert not any(" restart" in c for c in r["calls"]) and not r["left_over"]
    for sneaky in ("labape-ca; reboot", "labape-ca labape-api", "../labape-ca", ""):
        assert agent("restart_service", sneaky)["status"]["state"] == "failed"


def test_the_ca_is_restarted_and_the_stack_checked(agent):
    r = agent("restart_service", "labape-ca")
    assert r["status"]["state"] == "succeeded" and "restarted labape-ca" in r["status"]["message"], r["log"]
    assert r["status"]["requested_by"] == "tester" and r["status"]["action"] == "restart_service"
    assert any(c.endswith("compose restart labape-ca") for c in r["calls"])
    assert not any(c.startswith("stores.sh") for c in r["calls"])                 # the CA is not a store
    assert not r["left_over"]


@pytest.mark.parametrize("service", ["labape-openbao", "labape-conjur", "labape-conjur-tls"])
def test_a_store_is_restarted_and_unsealed_again(agent, service):
    r = agent("restart_service", service)
    assert r["status"]["state"] == "succeeded"
    restart = next(i for i, c in enumerate(r["calls"]) if c.endswith(f"compose restart {service}"))
    unseal = next(i for i, c in enumerate(r["calls"]) if c == "stores.sh unseal")
    assert restart < unseal                                                         # unsealed after, not before


def test_a_failed_restart_is_reported_and_nothing_is_unsealed(agent):
    r = agent("restart_service", "labape-openbao", STUB_RESTART_RC="1")
    assert r["status"]["state"] == "failed" and r["status"]["step"] == "applying" and "restarting labape-openbao failed" in r["status"]["message"]
    assert not any(c.startswith("stores.sh") for c in r["calls"]) and not r["left_over"]


def test_a_stack_that_does_not_come_back_healthy_is_a_failure(agent):
    r = agent("restart_service", "labape-ca", STUB_HEALTH_RC="1")
    assert r["status"]["state"] == "failed" and r["status"]["step"] == "health" and "did not pass" in r["status"]["message"]


def test_a_store_login_request_runs_stores_setup(agent):
    ok = agent("stores_setup")
    assert ok["status"]["state"] == "succeeded" and "stores.sh setup" in ok["status"]["message"]
    assert ok["calls"] == ["stores.sh setup [cli-check=warn]"]          # a drifted command-line vault warns, it does not stop it
    bad = agent("stores_setup", STUB_STORES_RC="1")
    assert bad["status"]["state"] == "failed" and "stores.sh setup failed" in bad["status"]["message"]
