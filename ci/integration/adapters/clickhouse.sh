# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="clickhouse"
ADAPTER_PACKAGE="datus-clickhouse"
ADAPTER_COMPOSE="datus-clickhouse/docker-compose.yml"
ADAPTER_TEST_PATH="datus-clickhouse/tests/integration"
ADAPTER_SERVICES=("clickhouse:300")

export_adapter_env() {
  export CLICKHOUSE_HTTP_HOST_PORT="${CLICKHOUSE_HTTP_HOST_PORT:-28123}"
  export CLICKHOUSE_NATIVE_HOST_PORT="${CLICKHOUSE_NATIVE_HOST_PORT:-29000}"
  export CLICKHOUSE_HOST="127.0.0.1"
  export CLICKHOUSE_PORT="$CLICKHOUSE_HTTP_HOST_PORT"
  export CLICKHOUSE_USER="default_user"
  export CLICKHOUSE_PASSWORD="default_test"
  export CLICKHOUSE_DATABASE="default_test"
}

adapter_env_summary() {
  echo "env: CLICKHOUSE_HOST=$CLICKHOUSE_HOST CLICKHOUSE_PORT=$CLICKHOUSE_PORT CLICKHOUSE_DATABASE=$CLICKHOUSE_DATABASE"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
