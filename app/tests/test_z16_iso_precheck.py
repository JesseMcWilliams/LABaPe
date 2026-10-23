"""create-iso-direct.sh says plainly when the install ISO isn't on the host, before it creates anything (found on the Director,
where virt-install's own message was 'Must specify storage creation parameters for non-existent path')."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "tofu" / "modules" / "vm" / "libvirt" / "scripts" / "create-iso-direct.sh"


def _bash() -> str | None:
    for c in (os.environ.get("LABAPE_TEST_BASH"), r"C:\Program Files\Git\bin\bash.exe", shutil.which("bash")):
        if c and (Path(c).exists() or shutil.which(c)):
            try:
                if subprocess.run([c, "-c", "echo ok"], capture_output=True, text=True, timeout=20).stdout.strip() == "ok":
                    return c
            except (OSError, subprocess.TimeoutExpired):
                continue
    return None


BASH = _bash()
pytestmark = pytest.mark.skipif(BASH is None, reason="no working bash")


def _run(tmp_path, iso: Path, uri="qemu:///system"):
    env = {**os.environ, "LIBVIRT_URI": uri, "VM_NAME": "Single1", "CPU_COUNT": "2", "MEMORY_MB": "4096", "DISK_GB": "40",
           "ISO_HOST_PATH": iso.as_posix(), "ANSWER_FILE_PATH": (tmp_path / "ks.cfg").as_posix(), "BRIDGE_DEVICE": "br0",
           "OS_FAMILY": "linux", "VM_STORAGE_PATH": (tmp_path / "vms").as_posix(), "LABAPE_WORKSPACE": "t"}
    return subprocess.run([BASH, SCRIPT.as_posix()], env=env, capture_output=True, text=True, timeout=120)


def test_a_missing_iso_is_named_with_what_to_do_and_nothing_is_created(tmp_path):
    r = _run(tmp_path, tmp_path / "Rocky-9-x86_64-minimal.iso")
    assert r.returncode == 1
    assert "install ISO for 'Single1' isn't on this host" in r.stderr and "Rocky-9-x86_64-minimal.iso" in r.stderr
    assert "Files" in r.stderr and "linked stack" in r.stderr
    assert not (tmp_path / "vms").exists()                                      # it stopped before creating the storage folder


def test_a_remote_libvirt_is_not_second_guessed(tmp_path):
    r = _run(tmp_path, tmp_path / "missing.iso", uri="qemu+ssh://kvm.example.test/system")
    assert "isn't on this host" not in r.stderr                                 # the path is not ours to look at


def test_an_iso_that_is_there_passes_the_check(tmp_path):
    iso = tmp_path / "there.iso"
    iso.write_bytes(b"x")
    r = _run(tmp_path, iso)
    assert "isn't on this host" not in r.stderr                                 # it goes on (and fails later without virt-install)
