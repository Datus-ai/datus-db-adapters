# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="tidb"
ADAPTER_PACKAGE="datus-tidb"
ADAPTER_COMPOSE="datus-tidb/docker-compose.yml"
ADAPTER_TEST_PATH="datus-tidb/tests/integration"
ADAPTER_SERVICES=("tidb:120")

export_adapter_env() {
  export TIDB_HOST_PORT="${TIDB_HOST_PORT:-24000}"
  export TIDB_STATUS_HOST_PORT="${TIDB_STATUS_HOST_PORT:-20080}"
  export TIDB_HOST="127.0.0.1"
  export TIDB_PORT="$TIDB_HOST_PORT"
  export TIDB_USER="root"
  export TIDB_PASSWORD=""
  export TIDB_DATABASE="test"
}

adapter_env_summary() {
  echo "env: TIDB_HOST=$TIDB_HOST TIDB_PORT=$TIDB_PORT TIDB_DATABASE=$TIDB_DATABASE"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
