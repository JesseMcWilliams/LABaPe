"""F1 (design §50.3): each KVM host's files path, its move, the free-space alert."""
import io
import sys
from pathlib import Path

from labape.engine import filedist, hostadmin
from labape.models import KvmHost

ROOT = Path(__file__).resolve().parents[2]


def test_the_files_path_beside_the_vm_folder():
    h = KvmHost(name="k", vm_storage_path="/data/VMs/LABaPe", files_path="")
    assert filedist.files_path(h) == "/data/Files"
    assert filedist.cache_dir(h, "isos") == "/data/Files/ISOs"
    assert filedist.cache_dir(h, "installers") == "/data/Files/Installers"
    assert filedist.cache_dir(h, "files") == "/data/Files/Other"
    assert filedist.legacy_dir(h, "isos") == "/data/VMs/LABaPe/.labape-isos"
    assert filedist.legacy_dir(h, "installers") == "/data/VMs/LABaPe/.labape-files/installers"
    assert filedist.files_path(KvmHost(name="k", vm_storage_path="/var/lib/libvirt/images", files_path="")) \
        == "/var/lib/libvirt/Files"
    assert filedist.files_path(KvmHost(name="k", vm_storage_path="/data/VMs/LABaPe", files_path="/srv/files/")) \
        == "/srv/files"
    sys.path.insert(0, str(ROOT / "deploy"))
    try:
        from labape_deploy.answers import files_path_for
    finally:
        sys.path.remove(str(ROOT / "deploy"))
    assert files_path_for("/data/VMs/LABaPe") == "/data/Files"          # the installer agrees


def test_an_old_cached_copy_is_moved_not_copied(db_reset, monkeypatch, tmp_path):
    from labape.db import session_factory
    with session_factory()() as db:
        prim = KvmHost(name="p", is_primary=True, vm_storage_path="/x")
        h = KvmHost(name="k", vm_storage_path=f"{tmp_path.as_posix()}/VMs/LABaPe")
        db.add_all([prim, h])
        db.commit()
        old = Path(filedist.legacy_dir(h, "isos")) / "rocky.iso"
        old.parent.mkdir(parents=True)
        old.write_bytes(b"x" * 10)
        new = f"{filedist.cache_dir(h, 'isos')}/rocky.iso"
        monkeypatch.setattr(filedist, "copy", lambda *a, **k: (_ for _ in ()).throw(AssertionError("copied")))
        log = io.StringIO()
        assert filedist.ensure(db, h, "/iso/rocky.iso", new, log, size=10, legacy=str(old)) == new
        assert Path(new).read_bytes() == b"x" * 10 and not old.exists() and "moved rocky.iso" in log.getvalue()


def test_moving_a_hosts_files(db_reset, tmp_path):
    h = KvmHost(name="k", vm_storage_path="/data/VMs/LABaPe", libvirt_uri="qemu:///system")
    old, new = tmp_path / "old", tmp_path / "new"
    (old / "ISOs").mkdir(parents=True)
    (old / "ISOs" / "a.iso").write_text("a")
    (old / "Other").mkdir()
    (old / "Other" / "b.txt").write_text("b")
    log = io.StringIO()
    import os
    if os.name == "nt":
        return                                     # the move is a POSIX shell script (the worker runs Linux)
    rc = filedist.move_files(None, h, str(old), str(new), log)
    assert rc == 0, log.getvalue()
    assert (new / "ISOs" / "a.iso").read_text() == "a" and (new / "Other" / "b.txt").read_text() == "b"
    assert (new / "Installers").is_dir() and not old.exists()


def test_free_space_warning_levels():
    w = hostadmin.warnings({}, {}, {"fs_used_pct": {"/data": 85, "/": 50, "/boot": 97}}, 20)
    assert [(x["level"], x["message"].split(" is ")[0]) for x in w] == [("warning", "/data"), ("error", "/boot")]
    assert hostadmin.warnings({}, {}, {"fs_used_pct": {"/data": 85}}, 10) == []


def test_pseudo_filesystems_are_left_out():
    w = hostadmin.warnings({}, {}, {"fs_used_pct": {"/sys/firmware/efi/efivars": 90.8, "/data": 85.3}}, 20)
    assert [x["message"] for x in w] == ["/data is 85.3% full (14.7% free; the alert is below 20%)"]
