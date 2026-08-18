# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="oracle"
ADAPTER_PACKAGE="datus-oracle"
ADAPTER_COMPOSE="datus-oracle/docker-compose.yml"
ADAPTER_TEST_PATH="datus-oracle/tests/integration"
ADAPTER_SERVICES=("oracle:1200")

export_adapter_env() {
  export ORACLE_HOST_PORT="${ORACLE_HOST_PORT:-21521}"
  export ORACLE_HOST="127.0.0.1"
  export ORACLE_PORT="$ORACLE_HOST_PORT"
  export ORACLE_USER="datus_test"
  export ORACLE_PASSWORD="test_password"
  export ORACLE_SID="${ORACLE_SID:-ORCLCDB}"
  export ORACLE_PDB="${ORACLE_PDB:-ORCLPDB1}"
  export ORACLE_SERVICE_NAME="${ORACLE_SERVICE_NAME:-$ORACLE_PDB}"
  export ORACLE_SCHEMA="DATUS_TEST"
  export ORACLE_SYS_PASSWORD="${ORACLE_SYS_PASSWORD:-test_sys_password}"
  export ORACLE_READY_TIMEOUT="${ORACLE_READY_TIMEOUT:-1200}"
}

adapter_env_summary() {
  echo "env: ORACLE_HOST=$ORACLE_HOST ORACLE_PORT=$ORACLE_PORT ORACLE_SERVICE_NAME=$ORACLE_SERVICE_NAME ORACLE_SCHEMA=$ORACLE_SCHEMA"
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
