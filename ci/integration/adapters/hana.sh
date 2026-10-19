# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="hana"
ADAPTER_PACKAGE="datus-hana"
ADAPTER_COMPOSE="datus-hana/docker-compose.yml"
ADAPTER_TEST_PATH="datus-hana/tests/integration"
ADAPTER_SERVICES=("hana:1200")

export_adapter_env() {
  export HANA_HOST_PORT="${HANA_HOST_PORT:-23941}"
  export HANA_HOST="127.0.0.1"
  export HANA_PORT="$HANA_HOST_PORT"
  export HANA_USER="DATUS_TEST"
  export HANA_SCHEMA="DATUS_TEST"
  export HANA_PASSWORD="${DATUS_TEST_HANA_PASSWORD:-}"
  export HANA_ENCRYPT="false"
  unset HANA_USERKEY HANA_DATABASE
}

prepare_adapter_dependencies() {
  # Bootstrap credentials must exist before Compose starts the image.
  sudo -n --preserve-env=DATUS_TEST_HANA_PASSWORD,HANA_MEMORY_LIMIT_GIB,HANA_MIN_FREE_DISK_GIB \
    python3 datus-hana/scripts/prepare_express.py
}

cleanup_adapter_test_artifacts() {
  # The shared runner invokes artifact cleanup before its exit-trap Compose stop.
  # HANA stores real database state in the bind mount, so stop it first.
  DATUS_TEST_HANA_PASSWORD="${DATUS_TEST_HANA_PASSWORD:-unused-for-compose-down}" \
    docker_compose -f "$ADAPTER_COMPOSE" down --remove-orphans >/dev/null
  sudo -n python3 "$ROOT_DIR/datus-hana/scripts/prepare_express.py" --cleanup
}

cleanup_adapter_stale_volumes() {
  # Also cover the shared runner's --cleanup-only and pre-run cleanup paths.
  cleanup_adapter_test_artifacts
}

adapter_env_summary() {
  echo "env: HANA_HOST=$HANA_HOST HANA_PORT=$HANA_PORT HANA_SCHEMA=$HANA_SCHEMA"
}

wait_for_adapter_client_readiness() {
  prepare_python_connector_environment "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
  uv run --package datus-hana python datus-hana/scripts/wait_for_hana.py
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
