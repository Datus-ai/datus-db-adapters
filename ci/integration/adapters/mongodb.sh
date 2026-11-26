# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="mongodb"
ADAPTER_PACKAGE="datus-mongodb"
ADAPTER_COMPOSE="datus-mongodb/docker-compose.yml"
ADAPTER_TEST_PATH="datus-mongodb/tests/integration"
ADAPTER_SERVICES=("mongodb:120")

export_adapter_env() {
  export MONGODB_HOST_PORT="${MONGODB_HOST_PORT:-47017}"
  export DATUS_TEST_MONGODB_USER="${DATUS_TEST_MONGODB_USER:-mongodb_test_admin}"
  export DATUS_TEST_MONGODB_PASSWORD="${DATUS_TEST_MONGODB_PASSWORD:-mongodb_test_password}"
  export MONGODB_TEST_USER="${MONGODB_TEST_USER:-$DATUS_TEST_MONGODB_USER}"
  export MONGODB_TEST_PASSWORD="${MONGODB_TEST_PASSWORD:-$DATUS_TEST_MONGODB_PASSWORD}"
}

adapter_env_summary() {
  echo "env: MONGODB_HOST_PORT=$MONGODB_HOST_PORT"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
