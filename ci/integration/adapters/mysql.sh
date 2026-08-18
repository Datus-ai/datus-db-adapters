# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="mysql"
ADAPTER_PACKAGE="datus-mysql"
ADAPTER_COMPOSE="datus-mysql/docker-compose.yml"
ADAPTER_TEST_PATH="datus-mysql/tests/integration"
ADAPTER_SERVICES=("mysql:300")

export_adapter_env() {
  export MYSQL_HOST_PORT="${MYSQL_HOST_PORT:-23306}"
  export MYSQL_HOST="127.0.0.1"
  export MYSQL_PORT="$MYSQL_HOST_PORT"
  export MYSQL_USER="test_user"
  export MYSQL_PASSWORD="test_password"
  export MYSQL_DATABASE="test"
}

adapter_env_summary() {
  echo "env: MYSQL_HOST=$MYSQL_HOST MYSQL_PORT=$MYSQL_PORT MYSQL_DATABASE=$MYSQL_DATABASE"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
