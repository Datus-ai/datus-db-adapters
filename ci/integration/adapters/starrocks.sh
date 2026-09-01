# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="starrocks"
ADAPTER_PACKAGE="datus-starrocks"
ADAPTER_COMPOSE="datus-starrocks/docker-compose.yml"
ADAPTER_TEST_PATH="datus-starrocks/tests/integration"
ADAPTER_SERVICES=("starrocks:600" "hive-metastore:600")

export_adapter_env() {
  export STARROCKS_QUERY_HOST_PORT="${STARROCKS_QUERY_HOST_PORT:-29030}"
  export STARROCKS_HTTP_HOST_PORT="${STARROCKS_HTTP_HOST_PORT:-28030}"
  export STARROCKS_HOST="127.0.0.1"
  export STARROCKS_PORT="$STARROCKS_QUERY_HOST_PORT"
  export STARROCKS_USER="root"
  export STARROCKS_PASSWORD=""
  export STARROCKS_CATALOG="default_catalog"
  export STARROCKS_DATABASE="test"
  # Resolved by the FE inside the compose network (embedded in CREATE EXTERNAL CATALOG).
  export HIVE_METASTORE_URI="thrift://hive-metastore:9083"
}

adapter_env_summary() {
  echo "env: STARROCKS_HOST=$STARROCKS_HOST STARROCKS_PORT=$STARROCKS_PORT STARROCKS_CATALOG=$STARROCKS_CATALOG STARROCKS_DATABASE=$STARROCKS_DATABASE"
}

wait_for_adapter_client_readiness() {
  uv run --package "$ADAPTER_PACKAGE" python datus-starrocks/scripts/wait_for_starrocks.py \
    --timeout "${STARROCKS_READY_TIMEOUT:-300}"
}
