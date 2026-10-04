# Stock Squid (Debian's OpenSSL build) for the shell sandbox's egress proxy.
# Kazi adds no proxy code: it only mounts a generated squid.conf and host list.
# Built on the sidecar host on first use, or ahead of time:
#   docker build -t kazi-egress-squid:1 Backend/orchestration/shell_exec/egress
FROM debian:bookworm-slim

# The certificate is required by Squid to open an ssl-bump port at all. It is
# never used: the config only peeks at the ClientHello, then splices or drops.
RUN apt-get update \
 && apt-get install -y --no-install-recommends squid-openssl openssl ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && openssl req -x509 -newkey rsa:2048 -nodes -days 3650 -subj "/CN=kazi-egress-unused" \
      -keyout /tmp/kazi-dummy.key -out /tmp/kazi-dummy.crt \
 && cat /tmp/kazi-dummy.key /tmp/kazi-dummy.crt > /etc/squid/kazi-dummy.pem \
 && rm /tmp/kazi-dummy.key /tmp/kazi-dummy.crt \
 && /usr/lib/squid/security_file_certgen -c -s /var/lib/kazi-ssl_db -M 4MB \
 && chown -R proxy:proxy /etc/squid/kazi-dummy.pem /var/lib/kazi-ssl_db \
 && chmod 600 /etc/squid/kazi-dummy.pem

USER proxy
ENTRYPOINT ["squid", "-N", "-f", "/etc/squid/kazi.conf"]
