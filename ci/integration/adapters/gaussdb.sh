# shellcheck shell=bash disable=SC2034

ADAPTER_NAME="gaussdb"
ADAPTER_PACKAGE="datus-gaussdb"
ADAPTER_COMPOSE="datus-gaussdb/docker-compose.yml"
ADAPTER_TEST_PATH="datus-gaussdb/tests/integration"
ADAPTER_SERVICES=("gaussdb:600")

export_adapter_env() {
  export GAUSSDB_HOST_PORT="${GAUSSDB_HOST_PORT:-25434}"
  export GAUSSDB_HOST="127.0.0.1"
  export GAUSSDB_PORT="$GAUSSDB_HOST_PORT"
  export GAUSSDB_USER="datus"
  export GAUSSDB_PASSWORD="Datus@123"
  export GAUSSDB_DATABASE="postgres"
}

adapter_env_summary() {
  echo "env: GAUSSDB_HOST=$GAUSSDB_HOST GAUSSDB_PORT=$GAUSSDB_PORT GAUSSDB_DATABASE=$GAUSSDB_DATABASE GAUSSDB_SSLMODE=${GAUSSDB_SSLMODE:-unset}"
}

prepare_adapter_dependencies() {
  local machine
  local operating_system
  local vendor_arch
  local library

  operating_system="$(uname -s)"
  if [ "$operating_system" != "Linux" ]; then
    echo "GaussDB vendored client libraries are Linux-only; using the pure-Python pg8000 driver on $operating_system"
    return 0
  fi

  machine="$(uname -m)"
  case "$machine" in
    x86_64|amd64) vendor_arch="x86_64" ;;
    aarch64|arm64) vendor_arch="aarch64" ;;
    *)
      echo "Unsupported architecture for GaussDB integration tests: $machine" >&2
      return 1
      ;;
  esac

  echo "Preparing GaussDB client libraries for $vendor_arch"
  require_command python3
  python3 datus-gaussdb/scripts/fetch_vendor_libpq.py --arch "$vendor_arch"
  for library in libpq.so.5 libssl.so.1.1 libcrypto.so.1.1; do
    if [ ! -f "datus-gaussdb/datus_gaussdb/_vendor/${vendor_arch}/${library}" ]; then
      echo "GaussDB client library was not vendored: $library" >&2
      return 1
    fi
  done
}

prepare_adapter_test_artifacts() {
  cleanup_adapter_test_artifacts
  GAUSSDB_TLS_HOST_DIR="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/datus-gaussdb-tls.XXXXXX")"
  docker_compose -f "$ADAPTER_COMPOSE" cp gaussdb:/gaussdb-tls/ca.crt "$GAUSSDB_TLS_HOST_DIR/ca.crt"
  docker_compose -f "$ADAPTER_COMPOSE" cp gaussdb:/gaussdb-tls/wrong-ca.crt "$GAUSSDB_TLS_HOST_DIR/wrong-ca.crt"
  # The native GaussDB/libpq client rejects CA files readable by group or other users.
  chmod 0600 "$GAUSSDB_TLS_HOST_DIR/ca.crt" "$GAUSSDB_TLS_HOST_DIR/wrong-ca.crt"
  export GAUSSDB_SSLMODE="verify-ca"
  export GAUSSDB_SSLROOTCERT="$GAUSSDB_TLS_HOST_DIR/ca.crt"
  export GAUSSDB_WRONG_SSLROOTCERT="$GAUSSDB_TLS_HOST_DIR/wrong-ca.crt"
}

cleanup_adapter_test_artifacts() {
  if [ -n "$GAUSSDB_TLS_HOST_DIR" ] && [ -d "$GAUSSDB_TLS_HOST_DIR" ]; then
    rm -f -- "$GAUSSDB_TLS_HOST_DIR/ca.crt" "$GAUSSDB_TLS_HOST_DIR/wrong-ca.crt"
    rmdir "$GAUSSDB_TLS_HOST_DIR" 2>/dev/null || true
  fi
  GAUSSDB_TLS_HOST_DIR=""
  unset GAUSSDB_SSLMODE GAUSSDB_SSLROOTCERT GAUSSDB_WRONG_SSLROOTCERT
}

wait_for_adapter_client_readiness() {
  wait_for_python_connector_readiness "$ADAPTER_NAME" "$ADAPTER_PACKAGE"
}
