# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="oracle"
ADAPTER_PACKAGE="datus-oracle"
ADAPTER_COMPOSE="datus-oracle/docker-compose.yml"
ADAPTER_TEST_PATH="datus-oracle/tests/integration"
ADAPTER_SERVICES=("oracle:1200")
# An empty Oracle database is multi-GB of system datafiles; expanding them into
# a fresh volume dominates the minutes-long cold start. Keep the data volume
# between runs — the docker/init scripts are idempotent and re-align state on
# every start. DATUS_RECREATE_VOLUMES=1 forces a clean volume (e.g. after a
# killed run leaves corrupt datafiles).
ADAPTER_PRESERVE_VOLUMES=1

export_adapter_env() {
  export ORACLE_HOST_PORT="${ORACLE_HOST_PORT:-21521}"
  export ORACLE_HOST="127.0.0.1"
  export ORACLE_PORT="$ORACLE_HOST_PORT"
  export ORACLE_USER="datus_test"
  export ORACLE_PASSWORD="test_password"
  export ORACLE_PDB="${ORACLE_PDB:-FREEPDB1}"
  export ORACLE_SERVICE_NAME="${ORACLE_SERVICE_NAME:-$ORACLE_PDB}"
  export ORACLE_SCHEMA="DATUS_TEST"
  export ORACLE_SYS_PASSWORD="${ORACLE_SYS_PASSWORD:-test_sys_password}"
  export ORACLE_READY_TIMEOUT="${ORACLE_READY_TIMEOUT:-1200}"
  # Container-side namespace; must match the client-side values above.
  export DATUS_TEST_ORACLE_USER="$ORACLE_USER"
  export DATUS_TEST_ORACLE_PASSWORD="$ORACLE_PASSWORD"
  export DATUS_TEST_ORACLE_SYS_PASSWORD="$ORACLE_SYS_PASSWORD"
  # Key the preserved data volume by image so bumping ORACLE_IMAGE starts from
  # a fresh volume (Oracle 19c and 23ai data files are not compatible).
  local oracle_image="${ORACLE_IMAGE:-gvenzl/oracle-free:23-slim}"
  export ORACLE_DATA_VOLUME_KEY="${ORACLE_DATA_VOLUME_KEY:-$(printf '%s' "$oracle_image" | tr -c 'a-zA-Z0-9' '-')}"
}

cleanup_adapter_stale_volumes() {
  local current="datus-oracle-data-${ORACLE_DATA_VOLUME_KEY}"
  local volume_name
  # Drop volumes keyed for other (older) images, plus the legacy pre-keying
  # project-scoped volume name.
  docker volume ls -q --filter "name=datus-oracle-data-" 2>/dev/null |
    while IFS= read -r volume_name; do
      [ "$volume_name" = "$current" ] && continue
      docker volume rm "$volume_name" >/dev/null 2>&1 || true
    done
  docker volume rm datus-oracle_oracle23free_data >/dev/null 2>&1 || true
}

adapter_env_summary() {
  echo "env: ORACLE_HOST=$ORACLE_HOST ORACLE_PORT=$ORACLE_PORT ORACLE_SERVICE_NAME=$ORACLE_SERVICE_NAME ORACLE_SCHEMA=$ORACLE_SCHEMA"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
