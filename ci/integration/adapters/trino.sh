# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="trino"
ADAPTER_PACKAGE="datus-trino"
ADAPTER_COMPOSE="datus-trino/docker-compose.yml"
ADAPTER_TEST_PATH="datus-trino/tests/integration"
ADAPTER_SERVICES=("trino:300")

export_adapter_env() {
  export TRINO_HOST_PORT="${TRINO_HOST_PORT:-28080}"
  export TRINO_HOST="127.0.0.1"
  export TRINO_PORT="$TRINO_HOST_PORT"
  export TRINO_USER="trino"
  export TRINO_PASSWORD=""
  export TRINO_CATALOG="tpch"
  export TRINO_SCHEMA="tiny"
  export TRINO_HTTP_SCHEME="http"
}

adapter_env_summary() {
  echo "env: TRINO_HOST=$TRINO_HOST TRINO_PORT=$TRINO_PORT TRINO_CATALOG=$TRINO_CATALOG TRINO_SCHEMA=$TRINO_SCHEMA"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
