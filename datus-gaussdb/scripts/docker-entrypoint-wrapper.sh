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
set -uo pipefail

DATA=${PGDATA:-/var/lib/opengauss/data}
GAUSSHOME=/usr/local/opengauss
GAUSSLOG=${GAUSSLOG:-/gausslog}
DB_USER=${GAUSSDB_USER:-datus}
DB_PASSWORD=${GAUSSDB_PASSWORD:-Datus@123}
PROVISIONED_MARKER=/tmp/datus-gaussdb-provisioned

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
    [ -f "$PROVISIONED_MARKER" ] || exit 1
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

run_provision_step() {
    local description=$1
    shift
    if ! as_omm "$@"; then
        echo "gaussdb: provisioning step failed: $description" >&2
        return 1
    fi
}

provision() {
    local listen_addresses
    local role_exists

    if ! wait_for_server; then
        echo "gaussdb: server never became ready, skipping provisioning" >&2
        return 1
    fi

    if ! role_exists="$(as_omm gsql -d postgres -tAc "select 1 from pg_roles where rolname = '$(sql_literal "$DB_USER")'")"; then
        echo "gaussdb: failed to inspect the integration-test role" >&2
        return 1
    fi
    if [ -z "$role_exists" ]; then
        run_provision_step \
            "create integration-test role" \
            gsql -d postgres -c "CREATE USER \"$DB_USER\" WITH LOGIN PASSWORD '$(sql_literal "$DB_PASSWORD")'" || return 1
        run_provision_step \
            "grant integration-test role privileges" \
            gsql -d postgres -c "GRANT ALL PRIVILEGES TO \"$DB_USER\"" || return 1
        echo "gaussdb: created test user '$DB_USER'"
    fi

    # openGauss, unlike PostgreSQL, grants ordinary roles no CREATE on the
    # public schema, so integration tests cannot create tables without this.
    # GRANT is idempotent, so it also repairs containers provisioned earlier.
    run_provision_step \
        "grant public schema privileges" \
        gsql -d postgres -c "GRANT ALL ON SCHEMA public TO \"$DB_USER\"" >/dev/null || return 1

    if ! grep -q "^host all $DB_USER " "$DATA/pg_hba.conf"; then
        if ! echo "host all $DB_USER 0.0.0.0/0 sha256" >>"$DATA/pg_hba.conf"; then
            echo "gaussdb: failed to update pg_hba.conf" >&2
            return 1
        fi
        run_provision_step "reload pg_hba.conf" gs_ctl reload -D "$DATA" >/dev/null || return 1
    fi

    # The image writes listen_addresses='*' before initdb, so this is normally a
    # no-op; it only fires on images/volumes configured otherwise.
    if ! listen_addresses="$(as_omm gsql -d postgres -tAc 'show listen_addresses')"; then
        echo "gaussdb: failed to inspect listen_addresses" >&2
        return 1
    fi
    if [ "$listen_addresses" != "*" ]; then
        run_provision_step "configure listen_addresses" gs_guc set -D "$DATA" -c "listen_addresses='*'" >/dev/null || return 1
        run_provision_step "restart after listen_addresses update" gs_ctl restart -D "$DATA" -Z single_node >/dev/null || return 1
    fi

    if ! touch "$PROVISIONED_MARKER"; then
        echo "gaussdb: failed to record successful provisioning" >&2
        return 1
    fi
    echo "gaussdb: ready for connections as '$DB_USER'"
}

mkdir -p "$GAUSSLOG"
chown omm:omm "$GAUSSLOG"

rm -f "$PROVISIONED_MARKER"
if ! provision; then
    echo "gaussdb: provisioning failed; healthcheck will remain unhealthy" >&2
fi &

if bash /entrypoint.sh gaussdb; then
    exit 0
fi

echo "gaussdb: image entrypoint exited non-zero, retrying server start" >&2
as_omm gs_ctl start -D "$DATA" -Z single_node || exit 1
tail -f /dev/null
