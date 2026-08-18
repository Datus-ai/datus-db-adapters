# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="hive"
ADAPTER_PACKAGE="datus-hive"
ADAPTER_COMPOSE="datus-hive/docker-compose.yml"
ADAPTER_TEST_PATH="datus-hive/tests/integration"
ADAPTER_SERVICES=("hive-metastore:600" "hive-server:900")

export_adapter_env() {
  export HIVE_METASTORE_HOST_PORT="${HIVE_METASTORE_HOST_PORT:-29083}"
  export HIVE_THRIFT_HOST_PORT="${HIVE_THRIFT_HOST_PORT:-20000}"
  export HIVE_WEBUI_HOST_PORT="${HIVE_WEBUI_HOST_PORT:-20002}"
  export HIVE_HOST="127.0.0.1"
  export HIVE_PORT="$HIVE_THRIFT_HOST_PORT"
  export HIVE_USERNAME="hive"
  export HIVE_PASSWORD=""
  export HIVE_DATABASE="default"
}

adapter_env_summary() {
  echo "env: HIVE_HOST=$HIVE_HOST HIVE_PORT=$HIVE_PORT HIVE_DATABASE=$HIVE_DATABASE"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
