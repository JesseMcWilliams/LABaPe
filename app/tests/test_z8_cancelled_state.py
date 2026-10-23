"""A job cancelled while it ran ends `cancelled`, even when it ran in the session that loaded it before the cancel
(system jobs such as linked.copy; found live 2026-10-23: a cancelled copy was recorded as failed)."""
from labape.db import session_factory
from labape.engine import runner
from labape.models import Job


def test_a_cancel_that_arrives_while_the_job_runs_is_seen(client):
    with session_factory()() as db:
        job = Job(type="linked.copy", requested_by="admin", params={}, state="running")
        db.add(job)
        db.commit()
        jid = job.id
        with session_factory()() as other:              # the Cancel button, in another request
            other.get(Job, jid).cancel_requested = True
            other.commit()
        assert job.cancel_requested is False            # this session's copy is stale
        runner._finish(db, job, None, 130)
        assert (job.state, job.exit_code) == ("cancelled", 130)


def test_a_failure_without_a_cancel_is_still_failed(client):
    with session_factory()() as db:
        job = Job(type="linked.copy", requested_by="admin", params={}, state="running")
        db.add(job)
        db.commit()
        runner._finish(db, job, None, 1)
        assert job.state == "failed"
        job2 = Job(type="linked.copy", requested_by="admin", params={}, state="running")
        db.add(job2)
        db.commit()
        runner._finish(db, job2, None, 0)
        assert job2.state == "succeeded"
