# The stack's syslog collector (design §29.1, questions 121-139): rsyslog
# receiving on UDP/TCP 514 and TLS 6514 and writing to its own PostgreSQL
# (labape-syslog-db, over a Unix socket). LABaPe renders the listener and
# forwarding settings into /etc/rsyslog-labape and issues the certificates
# into /tls; the entrypoint restarts rsyslogd when either changes.
FROM docker.io/library/debian:trixie-slim

RUN echo "rsyslog-pgsql rsyslog-pgsql/dbconfig-install boolean false" | debconf-set-selections \
 && apt-get update \
 && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
      rsyslog rsyslog-pgsql rsyslog-openssl ca-certificates procps \
 && rm -rf /var/lib/apt/lists/* /etc/rsyslog.d/* \
 && mkdir -p /var/spool/rsyslog /etc/rsyslog-labape /tls

COPY container/syslog/rsyslog.conf /etc/rsyslog.conf
COPY container/syslog/entrypoint.sh /usr/local/bin/labape-syslog
RUN chmod 0755 /usr/local/bin/labape-syslog

EXPOSE 514/udp 514/tcp 6514/tcp
ENTRYPOINT ["/usr/local/bin/labape-syslog"]
