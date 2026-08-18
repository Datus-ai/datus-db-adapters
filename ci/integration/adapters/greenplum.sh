# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="greenplum"
ADAPTER_PACKAGE="datus-greenplum"
ADAPTER_COMPOSE="datus-greenplum/docker-compose.yml"
ADAPTER_TEST_PATH="datus-greenplum/tests/integration"
ADAPTER_SERVICES=("greenplum:600")

export_adapter_env() {
  export GREENPLUM_HOST_PORT="${GREENPLUM_HOST_PORT:-25433}"
  export GREENPLUM_HOST="127.0.0.1"
  export GREENPLUM_PORT="$GREENPLUM_HOST_PORT"
  export GREENPLUM_USER="gpadmin"
  export GREENPLUM_PASSWORD="pivotal"
  export GREENPLUM_DATABASE="test"
  export GREENPLUM_SCHEMA="public"
}

adapter_env_summary() {
  echo "env: GREENPLUM_HOST=$GREENPLUM_HOST GREENPLUM_PORT=$GREENPLUM_PORT GREENPLUM_DATABASE=$GREENPLUM_DATABASE GREENPLUM_SCHEMA=$GREENPLUM_SCHEMA"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
