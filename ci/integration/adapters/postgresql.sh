# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="postgresql"
ADAPTER_PACKAGE="datus-postgresql"
ADAPTER_COMPOSE="datus-postgresql/docker-compose.yml"
ADAPTER_TEST_PATH="datus-postgresql/tests/integration"
ADAPTER_SERVICES=("postgres:300")

export_adapter_env() {
  export POSTGRESQL_HOST_PORT="${POSTGRESQL_HOST_PORT:-25432}"
  export POSTGRESQL_HOST="127.0.0.1"
  export POSTGRESQL_PORT="$POSTGRESQL_HOST_PORT"
  export POSTGRESQL_USER="test_user"
  export POSTGRESQL_PASSWORD="test_password"
  export POSTGRESQL_DATABASE="test"
  export POSTGRESQL_SCHEMA="public"
  # Container-side namespace; must match the client-side values above.
  export DATUS_TEST_POSTGRESQL_USER="$POSTGRESQL_USER"
  export DATUS_TEST_POSTGRESQL_PASSWORD="$POSTGRESQL_PASSWORD"
  export DATUS_TEST_POSTGRESQL_DATABASE="$POSTGRESQL_DATABASE"
}

adapter_env_summary() {
  echo "env: POSTGRESQL_HOST=$POSTGRESQL_HOST POSTGRESQL_PORT=$POSTGRESQL_PORT POSTGRESQL_DATABASE=$POSTGRESQL_DATABASE POSTGRESQL_SCHEMA=$POSTGRESQL_SCHEMA"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
