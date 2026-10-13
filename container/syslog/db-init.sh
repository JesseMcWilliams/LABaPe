#!/bin/sh
# labape-syslog-db first start (design §29.1): the syslog database, owned by
# LABaPe's role (it creates the table and purges old rows), and an insert-only
# role for rsyslog. Socket only (no TCP listener): only containers that mount
# the socket volume can connect, so the two roles need no passwords.
set -eu
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<'SQL'
CREATE ROLE labape_syslog LOGIN;
CREATE ROLE rsyslog_writer LOGIN;
CREATE DATABASE syslog OWNER labape_syslog ENCODING 'UTF8' TEMPLATE template0;
SQL
cat > "$PGDATA/pg_hba.conf" <<'EOF'
# LABaPe syslog database: Unix socket only (listen_addresses is empty).
local syslog labape_syslog trust
local syslog rsyslog_writer trust
local all    postgres      trust
EOF
