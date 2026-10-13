"""The primary file host (design §29.4, questions 131-135): one KVM host holds every file;
others get copies when a job needs them, or see the files through a share.

Revision ID: 0011_primary_file_host
Revises: 0010_host_log_forwarding
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_primary_file_host"
down_revision = "0010_host_log_forwarding"
branch_labels = None
depends_on = None

COLUMNS = (
    sa.Column("is_primary", sa.Boolean, nullable=False, server_default=sa.false()),
    sa.Column("files_from_share", sa.Boolean, nullable=False, server_default=sa.false()),
)


def upgrade() -> None:
    bind = op.get_bind()
    have = {c["name"] for c in sa.inspect(bind).get_columns("kvm_hosts")}
    for col in COLUMNS:
        if col.name not in have:
            op.add_column("kvm_hosts", col)
    # Until now every file lived beside the stack: the host the stack runs on (qemu:///system)
    # is the primary.
    if not bind.execute(sa.text("SELECT 1 FROM kvm_hosts WHERE is_primary")).first():
        bind.execute(sa.text("UPDATE kvm_hosts SET is_primary = :t WHERE id = (SELECT min(id) FROM kvm_hosts "
                             "WHERE libvirt_uri = 'qemu:///system')"), {"t": True})


def downgrade() -> None:
    for col in COLUMNS:
        op.drop_column("kvm_hosts", col.name)
