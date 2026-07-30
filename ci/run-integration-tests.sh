#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ALL_ADAPTERS=(postgresql mysql clickhouse starrocks doris trino greenplum hive spark)
DOCKER_COMPOSE=()
STARTED_ADAPTERS=()
CURRENT_ADAPTER=""

usage() {
  cat <<'USAGE'
Usage: ci/run-integration-tests.sh [--list] [--dry-run] [--changed base-ref] [adapter ...]
       ci/run-integration-tests.sh --cleanup-only

Runs Docker-backed DB adapter integration tests.

Options:
  --changed REF    Select impacted adapters from git diff REF...HEAD.
  --list           List configured adapter targets.
  --dry-run        Print selected adapters without starting Docker.
  --cleanup-only   Stop all configured integration compose projects.
  -h, --help       Show this help.
USAGE
}

require_command() {
  local command_name="$1"
  if ! command -v "$command_name" >/dev/null 2>&1; then
    echo "Missing required command: $command_name" >&2
    exit 127
  fi
}

detect_docker_compose() {
  if docker compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE=(docker compose)
    return 0
  fi
  if command -v docker-compose >/dev/null 2>&1 && docker-compose version >/dev/null 2>&1; then
    DOCKER_COMPOSE=(docker-compose)
    return 0
  fi
  return 1
}

install_docker_compose() {
  local version="${DOCKER_COMPOSE_VERSION:-v2.32.4}"
  local os
  local machine
  local arch
  local bin_dir
  local bin_path
  local url

  if ! command -v curl >/dev/null 2>&1; then
    echo "Missing required command: curl; cannot install Docker Compose." >&2
    return 1
  fi

  os="$(uname -s | tr '[:upper:]' '[:lower:]')"
  case "$os" in
    linux|darwin) ;;
    *)
      echo "Unsupported OS for automatic Docker Compose install: $os" >&2
      return 1
      ;;
  esac

  machine="$(uname -m)"
  case "$machine" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
    *)
      echo "Unsupported architecture for automatic Docker Compose install: $machine" >&2
      return 1
      ;;
  esac

  bin_dir="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/datus-docker-compose"
  bin_path="$bin_dir/docker-compose-$version-$os-$arch"
  url="https://github.com/docker/compose/releases/download/$version/docker-compose-$os-$arch"

  mkdir -p "$bin_dir"
  if [ ! -x "$bin_path" ]; then
    echo "Installing Docker Compose $version to $bin_path"
    curl -fsSL --retry 3 -o "$bin_path" "$url"
    chmod +x "$bin_path"
  fi

  DOCKER_COMPOSE=("$bin_path")
}

ensure_docker_compose() {
  detect_docker_compose || install_docker_compose
}

docker_compose() {
  if [ "${#DOCKER_COMPOSE[@]}" -eq 0 ]; then
    if ! ensure_docker_compose; then
      echo "Docker Compose is not available through 'docker compose' or 'docker-compose'." >&2
      return 127
    fi
  fi
  "${DOCKER_COMPOSE[@]}" "$@"
}

preflight() {
  require_command uv
  require_command docker
  if ! docker info >/dev/null 2>&1; then
    echo "Docker daemon is not reachable. Start Docker and retry." >&2
    exit 1
  fi
  if ! ensure_docker_compose; then
    echo "Docker Compose is not available through 'docker compose' or 'docker-compose'." >&2
    exit 1
  fi
}

is_known_adapter() {
  local requested="$1"
  local adapter
  for adapter in "${ALL_ADAPTERS[@]}"; do
    if [ "$adapter" = "$requested" ]; then
      return 0
    fi
  done
  return 1
}

adapter_package() {
  echo "datus-$1"
}

adapter_compose() {
  echo "datus-$1/docker-compose.yml"
}

adapter_test_path() {
  echo "datus-$1/tests/integration"
}

adapter_services() {
  case "$1" in
    postgresql) echo "postgres:300" ;;
    mysql) echo "mysql:300" ;;
    clickhouse) echo "clickhouse:300" ;;
    starrocks) echo "starrocks:600" ;;
    doris) echo "doris-fe:600 doris-be:600 hive-metastore:600" ;;
    trino) echo "trino:300" ;;
    greenplum) echo "greenplum:600" ;;
    hive) echo "hive-metastore:600 hive-server:900" ;;
    spark) echo "spark-thrift:900" ;;
    *) echo "Unknown adapter '$1'" >&2; return 1 ;;
  esac
}

list_adapters() {
  local adapter
  for adapter in "${ALL_ADAPTERS[@]}"; do
    printf '%s\t%s\t%s\t%s\n' \
      "$adapter" \
      "$(adapter_package "$adapter")" \
      "$(adapter_compose "$adapter")" \
      "$(adapter_test_path "$adapter")"
  done
}

export_adapter_env() {
  case "$1" in
    postgresql)
      export POSTGRESQL_HOST_PORT="${POSTGRESQL_HOST_PORT:-25432}"
      export POSTGRESQL_HOST="127.0.0.1"
      export POSTGRESQL_PORT="$POSTGRESQL_HOST_PORT"
      export POSTGRESQL_USER="test_user"
      export POSTGRESQL_PASSWORD="test_password"
      export POSTGRESQL_DATABASE="test"
      export POSTGRESQL_SCHEMA="public"
      ;;
    mysql)
      export MYSQL_HOST_PORT="${MYSQL_HOST_PORT:-23306}"
      export MYSQL_HOST="127.0.0.1"
      export MYSQL_PORT="$MYSQL_HOST_PORT"
      export MYSQL_USER="test_user"
      export MYSQL_PASSWORD="test_password"
      export MYSQL_DATABASE="test"
      ;;
    clickhouse)
      export CLICKHOUSE_HTTP_HOST_PORT="${CLICKHOUSE_HTTP_HOST_PORT:-28123}"
      export CLICKHOUSE_NATIVE_HOST_PORT="${CLICKHOUSE_NATIVE_HOST_PORT:-29000}"
      export CLICKHOUSE_HOST="127.0.0.1"
      export CLICKHOUSE_PORT="$CLICKHOUSE_HTTP_HOST_PORT"
      export CLICKHOUSE_USER="default_user"
      export CLICKHOUSE_PASSWORD="default_test"
      export CLICKHOUSE_DATABASE="default_test"
      ;;
    starrocks)
      export STARROCKS_QUERY_HOST_PORT="${STARROCKS_QUERY_HOST_PORT:-29030}"
      export STARROCKS_HTTP_HOST_PORT="${STARROCKS_HTTP_HOST_PORT:-28030}"
      export STARROCKS_HOST="127.0.0.1"
      export STARROCKS_PORT="$STARROCKS_QUERY_HOST_PORT"
      export STARROCKS_USER="root"
      export STARROCKS_PASSWORD=""
      export STARROCKS_CATALOG="default_catalog"
      export STARROCKS_DATABASE="test"
      ;;
    doris)
      export DORIS_QUERY_HOST_PORT="${DORIS_QUERY_HOST_PORT:-49030}"
      export DORIS_HTTP_HOST_PORT="${DORIS_HTTP_HOST_PORT:-48030}"
      export DORIS_HOST="127.0.0.1"
      export DORIS_PORT="$DORIS_QUERY_HOST_PORT"
      export DORIS_USER="root"
      export DORIS_PASSWORD=""
      export DORIS_CATALOG="internal"
      export DORIS_DATABASE="test"
      export HIVE_METASTORE_URI="thrift://hive-metastore:9083"
      ;;
    trino)
      export TRINO_HOST_PORT="${TRINO_HOST_PORT:-28080}"
      export TRINO_HOST="127.0.0.1"
      export TRINO_PORT="$TRINO_HOST_PORT"
      export TRINO_USER="trino"
      export TRINO_PASSWORD=""
      export TRINO_CATALOG="tpch"
      export TRINO_SCHEMA="tiny"
      export TRINO_HTTP_SCHEME="http"
      ;;
    greenplum)
      export GREENPLUM_HOST_PORT="${GREENPLUM_HOST_PORT:-25433}"
      export GREENPLUM_HOST="127.0.0.1"
      export GREENPLUM_PORT="$GREENPLUM_HOST_PORT"
      export GREENPLUM_USER="gpadmin"
      export GREENPLUM_PASSWORD="pivotal"
      export GREENPLUM_DATABASE="test"
      export GREENPLUM_SCHEMA="public"
      ;;
    hive)
      export HIVE_METASTORE_HOST_PORT="${HIVE_METASTORE_HOST_PORT:-29083}"
      export HIVE_THRIFT_HOST_PORT="${HIVE_THRIFT_HOST_PORT:-20000}"
      export HIVE_WEBUI_HOST_PORT="${HIVE_WEBUI_HOST_PORT:-20002}"
      export HIVE_HOST="127.0.0.1"
      export HIVE_PORT="$HIVE_THRIFT_HOST_PORT"
      export HIVE_USERNAME="hive"
      export HIVE_PASSWORD=""
      export HIVE_DATABASE="default"
      ;;
    spark)
      export SPARK_THRIFT_HOST_PORT="${SPARK_THRIFT_HOST_PORT:-21000}"
      export SPARK_UI_HOST_PORT="${SPARK_UI_HOST_PORT:-24040}"
      export SPARK_HOST="127.0.0.1"
      export SPARK_PORT="$SPARK_THRIFT_HOST_PORT"
      export SPARK_USER="spark"
      export SPARK_PASSWORD=""
      export SPARK_DATABASE="default"
      export SPARK_AUTH_MECHANISM="NONE"
      ;;
  esac
}

adapter_env_summary() {
  case "$1" in
    postgresql) echo "env: POSTGRESQL_HOST=$POSTGRESQL_HOST POSTGRESQL_PORT=$POSTGRESQL_PORT POSTGRESQL_DATABASE=$POSTGRESQL_DATABASE POSTGRESQL_SCHEMA=$POSTGRESQL_SCHEMA" ;;
    mysql) echo "env: MYSQL_HOST=$MYSQL_HOST MYSQL_PORT=$MYSQL_PORT MYSQL_DATABASE=$MYSQL_DATABASE" ;;
    clickhouse) echo "env: CLICKHOUSE_HOST=$CLICKHOUSE_HOST CLICKHOUSE_PORT=$CLICKHOUSE_PORT CLICKHOUSE_DATABASE=$CLICKHOUSE_DATABASE" ;;
    starrocks) echo "env: STARROCKS_HOST=$STARROCKS_HOST STARROCKS_PORT=$STARROCKS_PORT STARROCKS_CATALOG=$STARROCKS_CATALOG STARROCKS_DATABASE=$STARROCKS_DATABASE" ;;
    doris) echo "env: DORIS_HOST=$DORIS_HOST DORIS_PORT=$DORIS_PORT DORIS_CATALOG=$DORIS_CATALOG DORIS_DATABASE=$DORIS_DATABASE" ;;
    trino) echo "env: TRINO_HOST=$TRINO_HOST TRINO_PORT=$TRINO_PORT TRINO_CATALOG=$TRINO_CATALOG TRINO_SCHEMA=$TRINO_SCHEMA" ;;
    greenplum) echo "env: GREENPLUM_HOST=$GREENPLUM_HOST GREENPLUM_PORT=$GREENPLUM_PORT GREENPLUM_DATABASE=$GREENPLUM_DATABASE GREENPLUM_SCHEMA=$GREENPLUM_SCHEMA" ;;
    hive) echo "env: HIVE_HOST=$HIVE_HOST HIVE_PORT=$HIVE_PORT HIVE_DATABASE=$HIVE_DATABASE" ;;
    spark) echo "env: SPARK_HOST=$SPARK_HOST SPARK_PORT=$SPARK_PORT SPARK_DATABASE=$SPARK_DATABASE SPARK_AUTH_MECHANISM=$SPARK_AUTH_MECHANISM" ;;
  esac
}

compose_down() {
  local adapter="$1"
  local compose_file
  compose_file="$(adapter_compose "$adapter")"
  if [ -f "$compose_file" ]; then
    docker_compose -f "$compose_file" down -v --remove-orphans >/dev/null 2>&1 || true
  fi
}

cleanup_all() {
  local adapter
  for adapter in "${ALL_ADAPTERS[@]}"; do
    compose_down "$adapter"
  done
}

cleanup_started() {
  local adapter
  for adapter in "${STARTED_ADAPTERS[@]}"; do
    compose_down "$adapter"
  done
}

dump_adapter_diagnostics() {
  local adapter="$1"
  local compose_file
  local spec
  local service_name
  local container_id

  compose_file="$(adapter_compose "$adapter")"
  echo ""
  echo "=== Failure diagnostics: $adapter ===" >&2
  docker_compose -f "$compose_file" ps -a >&2 || true

  for spec in $(adapter_services "$adapter"); do
    service_name="${spec%%:*}"
    container_id="$(docker_compose -f "$compose_file" ps -a -q "$service_name" 2>/dev/null || true)"
    if [ -z "$container_id" ]; then
      echo "No container found for service '$service_name'." >&2
      continue
    fi

    printf "service=%s " "$service_name" >&2
    docker inspect --format \
      'container={{.Name}} status={{.State.Status}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} error={{.State.Error}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
      "$container_id" >&2 || true

    echo "--- Logs: $service_name ---" >&2
    docker_compose -f "$compose_file" logs --no-color --tail=300 "$service_name" >&2 || true
  done

  echo "--- Runner memory ---" >&2
  if command -v free >/dev/null 2>&1; then
    free -h >&2 || true
  elif command -v vm_stat >/dev/null 2>&1; then
    vm_stat >&2 || true
  else
    echo "No supported memory-reporting command found." >&2
  fi

  echo "--- Docker disk usage ---" >&2
  docker system df >&2 || true
  echo "--- Runner filesystem ---" >&2
  df -h "$ROOT_DIR" >&2 || true
}

cleanup_on_exit() {
  local exit_status=$?
  trap - EXIT

  if [ "$exit_status" -ne 0 ] && [ -n "$CURRENT_ADAPTER" ]; then
    dump_adapter_diagnostics "$CURRENT_ADAPTER"
  fi
  cleanup_started
  exit "$exit_status"
}

cleanup_only=0
dry_run=0
changed_mode=0
changed_base=""
requested_adapters=()

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cleanup-only)
      cleanup_only=1
      shift
      ;;
    --list)
      list_adapters
      exit 0
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    --changed)
      changed_mode=1
      if [ -z "${2:-}" ]; then
        echo "--changed requires a base ref" >&2
        exit 2
      fi
      changed_base="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      while [ "$#" -gt 0 ]; do
        requested_adapters+=("$1")
        shift
      done
      ;;
    -*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      requested_adapters+=("$1")
      shift
      ;;
  esac
done

if [ "$cleanup_only" -eq 1 ]; then
  cleanup_all
  exit 0
fi

wait_for_service_health() {
  local compose_file="$1"
  local service_name="$2"
  local timeout_seconds="$3"
  local container_id=""
  local status=""
  local deadline=$((SECONDS + timeout_seconds))

  container_id="$(docker_compose -f "$compose_file" ps -q "$service_name")"
  if [ -z "$container_id" ]; then
    echo "No container found for service '$service_name' in $compose_file" >&2
    docker_compose -f "$compose_file" ps || true
    return 1
  fi

  while [ "$SECONDS" -lt "$deadline" ]; do
    status="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$container_id" 2>/dev/null || echo unknown)"
    if [ "$status" = "healthy" ] || [ "$status" = "running" ]; then
      echo "Service '$service_name' is $status"
      return 0
    fi
    sleep 5
  done

  echo "Timed out waiting for service '$service_name' from $compose_file" >&2
  docker_compose -f "$compose_file" ps || true
  docker_compose -f "$compose_file" logs --tail=200 || true
  return 1
}

wait_for_adapter_client_readiness() {
  local adapter="$1"

  case "$adapter" in
    postgresql)
      wait_for_python_connector_readiness "postgresql" "datus-postgresql"
      ;;
    mysql)
      wait_for_python_connector_readiness "mysql" "datus-mysql"
      ;;
    clickhouse)
      wait_for_python_connector_readiness "clickhouse" "datus-clickhouse"
      ;;
    starrocks)
      uv run --package datus-starrocks python datus-starrocks/scripts/wait_for_starrocks.py --timeout "${STARROCKS_READY_TIMEOUT:-300}"
      ;;
    doris)
      uv run --package datus-doris python datus-doris/scripts/wait_for_doris.py --timeout "${DORIS_READY_TIMEOUT:-600}"
      ;;
    trino)
      wait_for_python_connector_readiness "trino" "datus-trino"
      ;;
    greenplum)
      wait_for_python_connector_readiness "greenplum" "datus-greenplum"
      ;;
    hive)
      wait_for_python_connector_readiness "hive" "datus-hive"
      ;;
    spark)
      wait_for_python_connector_readiness "spark" "datus-spark"
      ;;
  esac
}

wait_for_python_connector_readiness() {
  local adapter="$1"
  local package="$2"
  local timeout_env_name
  local timeout_seconds
  local deadline
  local probe_output

  timeout_env_name="$(echo "${adapter}_READY_TIMEOUT" | tr '[:lower:]' '[:upper:]')"
  timeout_seconds="${!timeout_env_name:-300}"
  deadline=$((SECONDS + timeout_seconds))
  probe_output="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/datus-${adapter}-readiness-$$.log"

  echo "Waiting for ${adapter} client readiness"
  while [ "$SECONDS" -lt "$deadline" ]; do
    if uv run --package "$package" --with pandas --with pyarrow python - "$adapter" <<'PY' >"$probe_output" 2>&1; then
import os
import sys

adapter = sys.argv[1]

if adapter == "postgresql":
    from datus_postgresql import PostgreSQLConfig, PostgreSQLConnector

    config = PostgreSQLConfig(
        host=os.getenv("POSTGRESQL_HOST", "127.0.0.1"),
        port=int(os.getenv("POSTGRESQL_PORT", "5432")),
        username=os.getenv("POSTGRESQL_USER", "test_user"),
        password=os.getenv("POSTGRESQL_PASSWORD", "test_password"),
        database=os.getenv("POSTGRESQL_DATABASE", "test"),
        schema_name=os.getenv("POSTGRESQL_SCHEMA", "public"),
        timeout_seconds=5,
    )
    connector = PostgreSQLConnector(config)
elif adapter == "mysql":
    from datus_mysql import MySQLConfig, MySQLConnector

    config = MySQLConfig(
        host=os.getenv("MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        username=os.getenv("MYSQL_USER", "test_user"),
        password=os.getenv("MYSQL_PASSWORD", "test_password"),
        database=os.getenv("MYSQL_DATABASE", "test"),
        timeout_seconds=5,
    )
    connector = MySQLConnector(config)
elif adapter == "clickhouse":
    from datus_clickhouse import ClickHouseConfig, ClickHouseConnector

    config = ClickHouseConfig(
        host=os.getenv("CLICKHOUSE_HOST", "127.0.0.1"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8123")),
        username=os.getenv("CLICKHOUSE_USER", "default_user"),
        password=os.getenv("CLICKHOUSE_PASSWORD", "default_test"),
        database=os.getenv("CLICKHOUSE_DATABASE", "default_test"),
        timeout_seconds=5,
    )
    connector = ClickHouseConnector(config)
elif adapter == "trino":
    from datus_trino import TrinoConfig, TrinoConnector

    config = TrinoConfig(
        host=os.getenv("TRINO_HOST", "127.0.0.1"),
        port=int(os.getenv("TRINO_PORT", "8080")),
        username=os.getenv("TRINO_USER", "trino"),
        password=os.getenv("TRINO_PASSWORD", ""),
        catalog=os.getenv("TRINO_CATALOG", "tpch"),
        schema_name=os.getenv("TRINO_SCHEMA", "tiny"),
        http_scheme=os.getenv("TRINO_HTTP_SCHEME", "http"),
        timeout_seconds=5,
    )
    connector = TrinoConnector(config)
elif adapter == "greenplum":
    from datus_greenplum import GreenplumConfig, GreenplumConnector

    config = GreenplumConfig(
        host=os.getenv("GREENPLUM_HOST", "127.0.0.1"),
        port=int(os.getenv("GREENPLUM_PORT", "5432")),
        username=os.getenv("GREENPLUM_USER", "gpadmin"),
        password=os.getenv("GREENPLUM_PASSWORD", "pivotal"),
        database=os.getenv("GREENPLUM_DATABASE", "test"),
        schema_name=os.getenv("GREENPLUM_SCHEMA", "public"),
        timeout_seconds=5,
    )
    connector = GreenplumConnector(config)
elif adapter == "hive":
    from datus_hive import HiveConfig, HiveConnector

    config = HiveConfig(
        host=os.getenv("HIVE_HOST", "127.0.0.1"),
        port=int(os.getenv("HIVE_PORT", "10000")),
        username=os.getenv("HIVE_USERNAME", "hive"),
        password=os.getenv("HIVE_PASSWORD", ""),
        database=os.getenv("HIVE_DATABASE", "default"),
        auth=os.getenv("HIVE_AUTH") or None,
        timeout_seconds=5,
    )
    connector = HiveConnector(config)
elif adapter == "spark":
    from datus_spark import SparkConfig, SparkConnector

    config = SparkConfig(
        host=os.getenv("SPARK_HOST", "127.0.0.1"),
        port=int(os.getenv("SPARK_PORT", "10000")),
        username=os.getenv("SPARK_USER", "spark"),
        password=os.getenv("SPARK_PASSWORD", ""),
        database=os.getenv("SPARK_DATABASE", "default"),
        auth_mechanism=os.getenv("SPARK_AUTH_MECHANISM", "NONE"),
        timeout_seconds=5,
    )
    connector = SparkConnector(config)
else:
    raise RuntimeError(f"unsupported adapter readiness probe: {adapter}")

try:
    if not connector.test_connection():
        raise RuntimeError(f"{adapter} connector readiness test returned false")
finally:
    connector.close()
PY
      echo "${adapter} client readiness probe succeeded"
      return 0
    fi
    sleep 5
  done

  echo "Timed out waiting for ${adapter} client readiness" >&2
  if [ -s "$probe_output" ]; then
    echo "Last ${adapter} readiness probe output:" >&2
    sed 's/^/  /' "$probe_output" >&2
  fi
  return 1
}

adapters_from_changed_files() {
  local base_ref="$1"
  local base_changed_files=""
  local staged_files=""
  local unstaged_files=""
  local untracked_files=""
  local changed_files=""

  if ! base_changed_files="$(git diff --name-only "${base_ref}...HEAD")"; then
    echo "Unable to determine changed adapters from base ref '$base_ref'." >&2
    return 1
  fi
  if ! staged_files="$(git diff --name-only --cached)"; then
    echo "Unable to determine staged adapter changes." >&2
    return 1
  fi
  if ! unstaged_files="$(git diff --name-only)"; then
    echo "Unable to determine unstaged adapter changes." >&2
    return 1
  fi
  if ! untracked_files="$(git ls-files --others --exclude-standard)"; then
    echo "Unable to determine untracked adapter changes." >&2
    return 1
  fi

  changed_files="$(
    printf '%s\n' \
      "$base_changed_files" \
      "$staged_files" \
      "$unstaged_files" \
      "$untracked_files" |
      awk 'NF && !seen[$0]++'
  )"

  if [ -z "$changed_files" ]; then
    return 0
  fi

  if echo "$changed_files" | grep -Eq '^(pyproject\.toml|ci/|\.github/workflows/|datus-db-core/|datus-sqlalchemy/)'; then
    printf '%s\n' "${ALL_ADAPTERS[@]}"
    return 0
  fi

  local adapter
  for adapter in "${ALL_ADAPTERS[@]}"; do
    if echo "$changed_files" | grep -Eq "^datus-${adapter}/"; then
      echo "$adapter"
    fi
  done
}

selected_adapters=()
if [ "$changed_mode" -eq 1 ]; then
  changed_adapters=""
  if ! changed_adapters="$(adapters_from_changed_files "$changed_base")"; then
    exit 1
  fi
  while IFS= read -r adapter; do
    [ -n "$adapter" ] && selected_adapters+=("$adapter")
  done < <(printf '%s\n' "$changed_adapters" | awk '!seen[$0]++')
else
  if [ "${#requested_adapters[@]}" -gt 0 ]; then
    selected_adapters=("${requested_adapters[@]}")
  fi
fi

if [ "${#selected_adapters[@]}" -eq 0 ] && [ "$changed_mode" -eq 1 ]; then
  echo "No local compose-backed adapter changes detected; skipping integration tests."
  exit 0
fi

if [ "${#selected_adapters[@]}" -eq 0 ]; then
  selected_adapters=("${ALL_ADAPTERS[@]}")
fi

for adapter in "${selected_adapters[@]}"; do
  if ! is_known_adapter "$adapter"; then
    echo "Unknown adapter '$adapter'. Use --list to see valid adapter names." >&2
    exit 2
  fi
done

if [ "$dry_run" -eq 1 ]; then
  for adapter in "${selected_adapters[@]}"; do
    export_adapter_env "$adapter"
    echo ""
    echo "=== Integration tests: $adapter ==="
    echo "package: $(adapter_package "$adapter")"
    echo "compose: $(adapter_compose "$adapter")"
    echo "tests: $(adapter_test_path "$adapter")"
    echo "services: $(adapter_services "$adapter")"
    adapter_env_summary "$adapter"
  done
  exit 0
fi

preflight
trap cleanup_on_exit EXIT

for adapter in "${selected_adapters[@]}"; do
  CURRENT_ADAPTER="$adapter"
  compose_file="$(adapter_compose "$adapter")"
  test_path="$(adapter_test_path "$adapter")"
  package="$(adapter_package "$adapter")"

  if [ ! -f "$compose_file" ]; then
    echo "Missing compose file for $adapter: $compose_file" >&2
    exit 1
  fi
  if [ ! -d "$test_path" ]; then
    echo "Missing integration test path for $adapter: $test_path" >&2
    exit 1
  fi

  echo ""
  echo "=== Integration tests: $adapter ==="
  compose_down "$adapter"
  STARTED_ADAPTERS+=("$adapter")
  export_adapter_env "$adapter"
  docker_compose -f "$compose_file" up -d --build

  for spec in $(adapter_services "$adapter"); do
    service_name="${spec%%:*}"
    timeout_seconds="${spec##*:}"
    wait_for_service_health "$compose_file" "$service_name" "$timeout_seconds"
  done
  wait_for_adapter_client_readiness "$adapter"

  uv run --package "$package" --with pytest --with pandas --with pyarrow pytest "$test_path" -m integration --tb=short --verbose

  compose_down "$adapter"
  CURRENT_ADAPTER=""
done
