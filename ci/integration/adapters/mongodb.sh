# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="mongodb"
ADAPTER_PACKAGE="datus-mongodb"
ADAPTER_COMPOSE="datus-mongodb/docker-compose.yml"
ADAPTER_TEST_PATH="datus-mongodb/tests/integration"
ADAPTER_SERVICES=("mongodb:120")

export_adapter_env() {
  export MONGODB_HOST_PORT="${MONGODB_HOST_PORT:-47017}"
}

adapter_env_summary() {
  echo "env: MONGODB_HOST_PORT=$MONGODB_HOST_PORT"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
