"""A job that needs a file this stack lacks gets it from a linked stack by itself (design §74): the install ISO of an OS,
and the installers of the applications chosen."""
import pytest

from labape import linked_library
from labape.db import session_factory
from labape.engine import downloads, linked_copy, runner
from labape.models import Environment, Job, StackLink, StoredFile


class Log:
    def __init__(self):
        self.text = ""

    def write(self, s):
        self.text += s


def _link(db, name="director.test"):
    link = StackLink(name=name, url=f"https://{name}", ca_pem="x", ca_fingerprint="0" * 64, inbound_hash="h")
    db.add(link)
    db.commit()
    return link


def _libs(link, files):
    return [{"link_id": link.id, "name": link.name, "url": link.url, "ok": True, "error": "", "templates": [], "files": files}]


ROCKY = {"id": 3, "category": "isos", "name": "Linux_Rocky-9.8-x86_64-minimal.iso", "size": 2_760_000_000, "sha256": "aa", "os_key": "rocky9"}
WIN = {"id": 14, "category": "isos", "name": "SERVER_2025.iso", "size": 8_150_000_000, "sha256": "bb", "os_key": "windows_server_2025"}


@pytest.fixture
def copies(monkeypatch):
    got = []
    monkeypatch.setattr(linked_copy, "copy_file", lambda db, link, item, log, actor, beat=None: got.append((link.name, item["name"], actor)))
    return got


def test_an_os_without_an_iso_gets_one_from_a_linked_stack(client, monkeypatch, copies):
    with session_factory()() as db:
        link = _link(db)
        monkeypatch.setattr(linked_library, "all_libraries", lambda db: _libs(link, [ROCKY, WIN]))
        log = Log()
        done = linked_copy.ensure_isos(db, {"rocky9"}, log, "tester")
    assert done == [ROCKY["name"]] and copies == [("director.test", ROCKY["name"], "tester")]
    assert "no ISO for rocky9 here; copying Linux_Rocky-9.8-x86_64-minimal.iso (2.8 GB) from the linked stack director.test" in log.text


def test_nothing_is_copied_when_the_iso_is_already_here_or_no_stack_has_one(client, monkeypatch, copies):
    with session_factory()() as db:
        link = _link(db)
        db.add(StoredFile(category="isos", name="mine.iso", path="/data/isos/mine.iso", os_key="rocky9", present=True))
        db.commit()
        monkeypatch.setattr(linked_library, "all_libraries", lambda db: _libs(link, [ROCKY]))
        assert linked_copy.ensure_isos(db, {"rocky9"}, Log()) == []                    # here already: the stacks are not even asked
        assert linked_copy.ensure_isos(db, {"ubuntu_lts"}, Log()) == []                # nobody has it: the job's own check says so
        assert linked_copy.ensure_isos(db, set(), Log()) == []
    assert copies == []


def test_the_default_there_wins_else_the_last_by_name(client, monkeypatch, copies):
    old = {**ROCKY, "id": 1, "name": "Linux_Rocky-9.6-minimal.iso"}
    new = {**ROCKY, "id": 2, "name": "Linux_Rocky-9.8-minimal.iso"}
    with session_factory()() as db:
        link = _link(db)
        monkeypatch.setattr(linked_library, "all_libraries", lambda db: _libs(link, [old, new]))
        linked_copy.ensure_isos(db, {"rocky9"}, Log())
        monkeypatch.setattr(linked_library, "all_libraries", lambda db: _libs(link, [{**old, "is_default": True}, new]))
        linked_copy.ensure_isos(db, {"rocky9"}, Log())
    assert [c[1] for c in copies] == ["Linux_Rocky-9.8-minimal.iso", "Linux_Rocky-9.6-minimal.iso"]


def test_a_stack_that_does_not_answer_is_skipped(client, monkeypatch, copies):
    with session_factory()() as db:
        down, up = _link(db, "down.test"), _link(db, "up.test")
        libs = [{"link_id": down.id, "name": "down.test", "url": down.url, "ok": False, "error": "timed out", "templates": [], "files": []},
                *_libs(up, [ROCKY])]
        monkeypatch.setattr(linked_library, "all_libraries", lambda db: libs)
        assert linked_copy.ensure_isos(db, {"rocky9"}, Log()) == [ROCKY["name"]]
    assert copies[0][0] == "up.test"


def test_content_already_stored_under_another_name_says_to_set_its_os(client, monkeypatch):
    def refuse(db, link, item, log, actor, beat=None):
        raise downloads.DownloadError("the same content is already stored as old.iso (isos); not kept")
    monkeypatch.setattr(linked_copy, "copy_file", refuse)
    with session_factory()() as db:
        link = _link(db)
        monkeypatch.setattr(linked_library, "all_libraries", lambda db: _libs(link, [ROCKY]))
        with pytest.raises(linked_copy.CopyError, match="already has .* under another name.*set its operating system"):
            linked_copy.ensure_isos(db, {"rocky9"}, Log())


def test_a_missing_installer_is_copied_by_name_and_sub_folders_are_left_alone(client, monkeypatch, copies):
    msi = {"id": 9, "category": "installers", "name": "tool-1.0.msi", "size": 30_000_000, "sha256": "cc", "targets": ["windows"]}
    with session_factory()() as db:
        link = _link(db)
        db.add(StoredFile(category="installers", name="have.msi", path="/data/installers/have.msi", present=True))
        db.commit()
        monkeypatch.setattr(linked_library, "all_libraries", lambda db: _libs(link, [msi, ROCKY]))
        done = linked_copy.ensure_installers(db, {"have.msi", "tool-1.0.msi", "sub/folder.msi", "nobody-has.msi"}, Log(), "tester")
    assert done == ["tool-1.0.msi"] and copies == [("director.test", "tool-1.0.msi", "tester")]


def test_the_runner_asks_only_for_what_a_job_installs_from(client, monkeypatch):
    seen = []
    monkeypatch.setattr(linked_copy, "ensure_isos", lambda db, keys, log, actor, beat=None: seen.append((set(keys), actor)) or [])
    with session_factory()() as db:
        env = Environment(name="iso1", kvm_host_id=1, created_by="admin", status="new", spec={"host_groups": [
            {"name": "a", "os": "rocky9", "image_source": "iso_direct"}, {"name": "b", "os": "debian_latest", "image_source": "packer_template"}]})
        db.add(env)
        db.commit()
        deploy = Job(type="environment.deploy", requested_by="adm", params={}, environment_id=env.id)
        build = Job(type="template.build", requested_by="adm", params={"os_key": "ubuntu_lts"})
        power = Job(type="environment.power", requested_by="adm", params={}, environment_id=env.id)
        db.add_all([deploy, build, power])
        db.commit()
        for job in (deploy, build, power):
            runner._copy_isos_from_links(db, job, env if job.environment_id else None)
    assert seen == [({"rocky9"}, "adm"), ({"ubuntu_lts"}, "adm")]                     # a power job installs nothing: no question asked


def test_a_failed_copy_stops_the_job_with_the_reason(client, monkeypatch):
    def boom(db, keys, log, actor, beat=None):
        raise linked_copy.CopyError("director.test has no space")
    monkeypatch.setattr(linked_copy, "ensure_isos", boom)
    with session_factory()() as db:
        job = Job(type="template.build", requested_by="adm", params={"os_key": "rocky9"})
        db.add(job)
        db.commit()
        with pytest.raises(RuntimeError, match="director.test has no space"):
            runner._copy_isos_from_links(db, job, None)
