# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="doris"
ADAPTER_PACKAGE="datus-doris"
ADAPTER_COMPOSE="datus-doris/docker-compose.yml"
ADAPTER_TEST_PATH="datus-doris/tests/integration"
ADAPTER_SERVICES=("doris:600" "hive-metastore:600")

export_adapter_env() {
  export DORIS_QUERY_HOST_PORT="${DORIS_QUERY_HOST_PORT:-49030}"
  export DORIS_HTTP_HOST_PORT="${DORIS_HTTP_HOST_PORT:-48030}"
  export DORIS_HOST="127.0.0.1"
  export DORIS_PORT="$DORIS_QUERY_HOST_PORT"
  export DORIS_USER="root"
  export DORIS_PASSWORD=""
  export DORIS_CATALOG="internal"
  export DORIS_DATABASE="test"
  export HIVE_METASTORE_URI="thrift://hive-metastore:9083"
}

adapter_env_summary() {
  echo "env: DORIS_HOST=$DORIS_HOST DORIS_PORT=$DORIS_PORT DORIS_CATALOG=$DORIS_CATALOG DORIS_DATABASE=$DORIS_DATABASE"
}

wait_for_adapter_client_readiness() {
  uv run --package "$ADAPTER_PACKAGE" python datus-doris/scripts/wait_for_doris.py \
    --timeout "${DORIS_READY_TIMEOUT:-600}"
}
