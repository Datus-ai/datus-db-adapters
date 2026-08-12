#!/usr/bin/env bash
# Entrypoint wrapper for the openGauss test container used by docker-compose.yml.
# See the comment block in docker-compose.yml for why this wrapper exists.
#
# Responsibilities:
#   1. Create /gausslog before the server starts (the server SIGABRTs when
#      GAUSSLOG is unset, and initdb fails when GAUSSLOG lives inside PGDATA).
#   2. Run the image entrypoint; if it aborts on the first post-initdb start
#      (reproducible on Docker Desktop for macOS), start the server again and
#      keep the container in the foreground.
#   3. Provision the integration-test login role, idempotently, once the server
#      accepts connections.
set -u

DATA=${PGDATA:-/var/lib/opengauss/data}
GAUSSHOME=/usr/local/opengauss
GAUSSLOG=${GAUSSLOG:-/gausslog}
DB_USER=${GAUSSDB_USER:-datus}
DB_PASSWORD=${GAUSSDB_PASSWORD:-Datus@123}

as_omm() {
    gosu omm env \
        HOME=/home/omm \
        GAUSSHOME="$GAUSSHOME" \
        PATH="$GAUSSHOME/bin:/usr/local/bin:/usr/bin:/bin" \
        LD_LIBRARY_PATH="$GAUSSHOME/lib" \
        GAUSSLOG="$GAUSSLOG" \
        PGPASSWORD="${GS_PASSWORD:-}" \
        "$@"
}

# Double every single quote so a value is safe inside a SQL string literal.
sql_literal() { printf "%s" "${1//\'/\'\'}"; }

if [ "${1:-}" = "healthcheck" ]; then
    as_omm gsql -d postgres -c 'select 1' >/dev/null || exit 1
    # Provisioning runs in the background, so an accepting server is not yet a
    # usable one: report healthy only once the test role exists, otherwise
    # tests start against an unprovisioned server and fail on authentication.
    [ -n "$(as_omm gsql -d postgres -tAc "select 1 from pg_roles where rolname = '$(sql_literal "$DB_USER")'")" ] || exit 1
    exit 0
fi

wait_for_server() {
    for _ in $(seq 1 90); do
        as_omm gsql -d postgres -c 'select 1' >/dev/null 2>&1 && return 0
        sleep 2
    done
    return 1
}

provision() {
    if ! wait_for_server; then
        echo "gaussdb: server never became ready, skipping provisioning" >&2
        return 1
    fi

    if [ -z "$(as_omm gsql -d postgres -tAc "select 1 from pg_roles where rolname = '$(sql_literal "$DB_USER")'")" ]; then
        as_omm gsql -d postgres -c "CREATE USER \"$DB_USER\" WITH LOGIN PASSWORD '$(sql_literal "$DB_PASSWORD")'"
        as_omm gsql -d postgres -c "GRANT ALL PRIVILEGES TO \"$DB_USER\""
        echo "gaussdb: created test user '$DB_USER'"
    fi

    # openGauss, unlike PostgreSQL, grants ordinary roles no CREATE on the
    # public schema, so integration tests cannot create tables without this.
    # GRANT is idempotent, so it also repairs containers provisioned earlier.
    as_omm gsql -d postgres -c "GRANT ALL ON SCHEMA public TO \"$DB_USER\"" >/dev/null

    if ! grep -q "^host all $DB_USER " "$DATA/pg_hba.conf"; then
        echo "host all $DB_USER 0.0.0.0/0 sha256" >>"$DATA/pg_hba.conf"
        as_omm gs_ctl reload -D "$DATA" >/dev/null
    fi

    # The image writes listen_addresses='*' before initdb, so this is normally a
    # no-op; it only fires on images/volumes configured otherwise.
    if [ "$(as_omm gsql -d postgres -tAc 'show listen_addresses')" != "*" ]; then
        as_omm gs_guc set -D "$DATA" -c "listen_addresses='*'" >/dev/null
        as_omm gs_ctl restart -D "$DATA" -Z single_node >/dev/null
    fi

    echo "gaussdb: ready for connections as '$DB_USER'"
}

mkdir -p "$GAUSSLOG"
chown omm:omm "$GAUSSLOG"

provision &

if bash /entrypoint.sh gaussdb; then
    exit 0
fi

echo "gaussdb: image entrypoint exited non-zero, retrying server start" >&2
as_omm gs_ctl start -D "$DATA" -Z single_node || exit 1
tail -f /dev/null
