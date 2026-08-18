# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="spark"
ADAPTER_PACKAGE="datus-spark"
ADAPTER_COMPOSE="datus-spark/docker-compose.yml"
ADAPTER_TEST_PATH="datus-spark/tests/integration"
ADAPTER_SERVICES=("spark-thrift:900")

export_adapter_env() {
  export SPARK_THRIFT_HOST_PORT="${SPARK_THRIFT_HOST_PORT:-21000}"
  export SPARK_UI_HOST_PORT="${SPARK_UI_HOST_PORT:-24040}"
  export SPARK_HOST="127.0.0.1"
  export SPARK_PORT="$SPARK_THRIFT_HOST_PORT"
  export SPARK_USER="spark"
  export SPARK_PASSWORD=""
  export SPARK_DATABASE="default"
  export SPARK_AUTH_MECHANISM="NONE"
}

adapter_env_summary() {
  echo "env: SPARK_HOST=$SPARK_HOST SPARK_PORT=$SPARK_PORT SPARK_DATABASE=$SPARK_DATABASE SPARK_AUTH_MECHANISM=$SPARK_AUTH_MECHANISM"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
