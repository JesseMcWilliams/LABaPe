"""Question 200: user grants and role bindings belong to the account they were made for, not to a name."""
from labape import permissions
from labape.db import session_factory
from labape.models import Grant, RoleBinding, User
from labape.security import Principal, adopt_subject, resolve_roles, subject_key


def _user(db, subject, name="bob"):
    u = User(provider="oidc", subject=subject, username=name, groups=[])
    db.add(u)
    db.commit()
    return u


def test_sign_in_ties_old_grants_and_bindings_to_the_account(client):
    with session_factory()() as db:
        db.add_all([Grant(object_type="environment", object_id=1, principal_type="user", principal="bob", level="owner"),
                    RoleBinding(principal_type="user", principal="bob", role="deployer")])
        db.commit()
        bob = _user(db, "sub-1")
        assert adopt_subject(db, bob) == 2
        assert {g.subject for g in db.query(Grant)} == {"oidc:sub-1"}
        assert adopt_subject(db, bob) == 0                      # nothing more to tie


def test_another_account_with_the_same_name_gets_nothing(client):
    with session_factory()() as db:
        db.add_all([Grant(object_type="environment", object_id=7, principal_type="user", principal="bob",
                          level="owner", subject="oidc:sub-1"),
                    RoleBinding(principal_type="user", principal="bob", role="deployer", subject="oidc:sub-1")])
        db.commit()
        old, new = _user(db, "sub-1"), None
        db.delete(old)
        db.commit()
        new = _user(db, "sub-2")                                # the name reused by someone else
        assert resolve_roles(db, "bob", [], subject_key(new)) == set()
        p = Principal(new.id, "bob", "Bob", [], set(), subject=subject_key(new))
        assert not permissions.can_see(db, p, "environment", 7)
        assert permissions.visible_ids(db, p, "environment") == set()
        mine = Principal(1, "bob", "Bob", [], set(), subject="oidc:sub-1")
        assert permissions.can_see(db, mine, "environment", 7)
        assert resolve_roles(db, "bob", [], "oidc:sub-1") == {"deployer"}


def test_a_renamed_account_keeps_its_grants_only_under_its_old_name_check(client):
    # The grant names "bob"; the account is now called "robert": the name no longer matches, as before.
    with session_factory()() as db:
        db.add(Grant(object_type="environment", object_id=3, principal_type="user", principal="bob",
                     level="owner", subject="oidc:sub-1"))
        db.commit()
        p = Principal(1, "robert", "Robert", [], set(), subject="oidc:sub-1")
        assert not permissions.can_see(db, p, "environment", 3)


def test_without_a_subject_the_check_falls_back_to_the_name(client):
    with session_factory()() as db:
        db.add(Grant(object_type="environment", object_id=4, principal_type="user", principal="bob",
                     level="owner", subject="oidc:sub-1"))
        db.commit()
        assert permissions.can_see(db, Principal(1, "bob", "Bob", [], set()), "environment", 4)


def test_new_owner_grants_are_tied_when_the_account_is_known(client):
    with session_factory()() as db:
        _user(db, "sub-9", "carol")
        permissions.add_owner(db, "environment", 5, "carol")
        permissions.add_owner(db, "environment", 6, "nobody-yet")
        subjects = {g.principal: g.subject for g in db.query(Grant)}
        assert subjects == {"carol": "oidc:sub-9", "nobody-yet": None}
