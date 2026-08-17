#!/usr/bin/env bash
# Executed by the upstream image during first-time init, after its temporary
# server starts and before the final server start. This guarantees that the
# final TCP listener requires TLS without adding a restart race to the wrapper.
set -euo pipefail

install -m 0600 /gaussdb-tls/server.key "$PGDATA/server.key"
# openGauss rejects both the private key and the server certificate when
# either is group/world-accessible.
install -m 0600 /gaussdb-tls/server.crt "$PGDATA/server.crt"

gs_guc set -D "$PGDATA" -c "ssl=on"
gs_guc set -D "$PGDATA" -c "require_ssl=on"
