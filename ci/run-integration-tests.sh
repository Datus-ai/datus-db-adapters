#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADAPTER_DEFINITION_DIR="$ROOT_DIR/ci/integration/adapters"
cd "$ROOT_DIR"

ALL_ADAPTERS=(postgresql mysql clickhouse starrocks doris trino greenplum hive spark oracle gaussdb)
DOCKER_COMPOSE=()
STARTED_ADAPTERS=()
CURRENT_ADAPTER=""
# shellcheck disable=SC2034  # Used by the sourced GaussDB adapter definition.
GAUSSDB_TLS_HOST_DIR=""

ADAPTER_NAME=""
ADAPTER_PACKAGE=""
ADAPTER_COMPOSE=""
ADAPTER_TEST_PATH=""
ADAPTER_SERVICES=()

usage() {
  cat <<'USAGE'
Usage: ci/run-integration-tests.sh [--list] [--dry-run] [--changed base-ref] [adapter ...]
       ci/run-integration-tests.sh --cleanup-only [adapter ...]

Runs Docker-backed DB adapter integration tests.

Options:
  --changed REF    Select impacted adapters from git diff REF...HEAD.
  --list           List configured adapter targets.
  --dry-run        Print selected adapters without starting Docker.
  --cleanup-only   Stop the requested adapters, or every adapter when none are given.
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

reset_adapter_definition() {
  unset -f \
    export_adapter_env \
    adapter_env_summary \
    prepare_adapter_dependencies \
    prepare_adapter_test_artifacts \
    cleanup_adapter_test_artifacts \
    wait_for_adapter_client_readiness 2>/dev/null || true

  ADAPTER_NAME=""
  ADAPTER_PACKAGE=""
  ADAPTER_COMPOSE=""
  ADAPTER_TEST_PATH=""
  ADAPTER_SERVICES=()

  prepare_adapter_dependencies() { :; }
  prepare_adapter_test_artifacts() { :; }
  cleanup_adapter_test_artifacts() { :; }
}

load_adapter() {
  local requested="$1"
  local definition="$ADAPTER_DEFINITION_DIR/$requested.sh"

  if ! is_known_adapter "$requested"; then
    echo "Unknown adapter '$requested'. Use --list to see valid adapter names." >&2
    return 1
  fi
  if [ ! -f "$definition" ]; then
    echo "Missing integration adapter definition: $definition" >&2
    return 1
  fi

  reset_adapter_definition
  # shellcheck source=/dev/null
  source "$definition"

  if [ "$ADAPTER_NAME" != "$requested" ]; then
    echo "Adapter definition $definition declared '$ADAPTER_NAME', expected '$requested'." >&2
    return 1
  fi
  if [ -z "$ADAPTER_PACKAGE" ] || [ -z "$ADAPTER_COMPOSE" ] || [ -z "$ADAPTER_TEST_PATH" ]; then
    echo "Adapter definition $definition is incomplete." >&2
    return 1
  fi
  if [ "${#ADAPTER_SERVICES[@]}" -eq 0 ]; then
    echo "Adapter definition $definition does not declare services." >&2
    return 1
  fi
  for required_function in export_adapter_env adapter_env_summary wait_for_adapter_client_readiness; do
    if ! declare -F "$required_function" >/dev/null; then
      echo "Adapter definition $definition is missing $required_function()." >&2
      return 1
    fi
  done
}

list_adapters() {
  local adapter
  for adapter in "${ALL_ADAPTERS[@]}"; do
    load_adapter "$adapter"
    printf '%s\t%s\t%s\t%s\n' "$ADAPTER_NAME" "$ADAPTER_PACKAGE" "$ADAPTER_COMPOSE" "$ADAPTER_TEST_PATH"
  done
}

compose_down() {
  local adapter="$1"
  if ! load_adapter "$adapter"; then
    return 0
  fi
  if [ -f "$ADAPTER_COMPOSE" ]; then
    docker_compose -f "$ADAPTER_COMPOSE" down -v --remove-orphans >/dev/null 2>&1 || true
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
  local spec
  local service_name
  local container_id

  if ! load_adapter "$adapter"; then
    return 0
  fi
  echo ""
  echo "=== Failure diagnostics: $adapter ===" >&2
  docker_compose -f "$ADAPTER_COMPOSE" ps -a >&2 || true

  for spec in "${ADAPTER_SERVICES[@]}"; do
    service_name="${spec%%:*}"
    container_id="$(docker_compose -f "$ADAPTER_COMPOSE" ps -a -q "$service_name" 2>/dev/null || true)"
    if [ -z "$container_id" ]; then
      echo "No container found for service '$service_name'." >&2
      continue
    fi

    printf "service=%s " "$service_name" >&2
    docker inspect --format \
      'container={{.Name}} status={{.State.Status}} exit_code={{.State.ExitCode}} oom_killed={{.State.OOMKilled}} error={{.State.Error}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' \
      "$container_id" >&2 || true

    echo "--- Logs: $service_name ---" >&2
    docker_compose -f "$ADAPTER_COMPOSE" logs --no-color --tail=300 "$service_name" >&2 || true
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

  if [ -n "$CURRENT_ADAPTER" ]; then
    if [ "$exit_status" -ne 0 ]; then
      dump_adapter_diagnostics "$CURRENT_ADAPTER"
    else
      load_adapter "$CURRENT_ADAPTER"
    fi
    cleanup_adapter_test_artifacts
  fi
  cleanup_started
  exit "$exit_status"
}

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

wait_for_python_connector_readiness() {
  local adapter="$1"
  local package="$2"
  local timeout_env_name
  local timeout_seconds
  local deadline
  local probe_output
  local probe="$ROOT_DIR/ci/integration/readiness/$adapter.py"

  if [ ! -f "$probe" ]; then
    echo "Missing readiness probe for $adapter: $probe" >&2
    return 1
  fi

  timeout_env_name="$(echo "${adapter}_READY_TIMEOUT" | tr '[:lower:]' '[:upper:]')"
  timeout_seconds="${!timeout_env_name:-300}"
  deadline=$((SECONDS + timeout_seconds))
  probe_output="${RUNNER_TEMP:-${TMPDIR:-/tmp}}/datus-${adapter}-readiness-$$.log"

  echo "Waiting for ${adapter} client readiness"
  while [ "$SECONDS" -lt "$deadline" ]; do
    if uv run --package "$package" --with pandas --with pyarrow python "$probe" >"$probe_output" 2>&1; then
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
  if [ "${#requested_adapters[@]}" -eq 0 ]; then
    cleanup_all
  else
    for adapter in "${requested_adapters[@]}"; do
      compose_down "$adapter"
    done
  fi
  exit 0
fi

selected_adapters=()
if [ "$changed_mode" -eq 1 ]; then
  changed_adapters=""
  if ! changed_adapters="$(python3 ci/select_affected.py --base "$changed_base" --suite integration --kind compose)"; then
    exit 1
  fi
  while IFS= read -r adapter; do
    [ -n "$adapter" ] && selected_adapters+=("$adapter")
  done < <(printf '%s\n' "$changed_adapters" | awk '!seen[$0]++')
elif [ "${#requested_adapters[@]}" -gt 0 ]; then
  selected_adapters=("${requested_adapters[@]}")
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
    load_adapter "$adapter"
    export_adapter_env
    echo ""
    echo "=== Integration tests: $ADAPTER_NAME ==="
    echo "package: $ADAPTER_PACKAGE"
    echo "compose: $ADAPTER_COMPOSE"
    echo "tests: $ADAPTER_TEST_PATH"
    echo "services: ${ADAPTER_SERVICES[*]}"
    adapter_env_summary
  done
  exit 0
fi

preflight
trap cleanup_on_exit EXIT

for adapter in "${selected_adapters[@]}"; do
  load_adapter "$adapter"
  CURRENT_ADAPTER="$adapter"

  if [ ! -f "$ADAPTER_COMPOSE" ]; then
    echo "Missing compose file for $adapter: $ADAPTER_COMPOSE" >&2
    exit 1
  fi
  if [ ! -d "$ADAPTER_TEST_PATH" ]; then
    echo "Missing integration test path for $adapter: $ADAPTER_TEST_PATH" >&2
    exit 1
  fi

  echo ""
  echo "=== Integration tests: $adapter ==="
  compose_down "$adapter"
  STARTED_ADAPTERS+=("$adapter")
  export_adapter_env
  prepare_adapter_dependencies
  docker_compose -f "$ADAPTER_COMPOSE" up -d --build

  for spec in "${ADAPTER_SERVICES[@]}"; do
    service_name="${spec%%:*}"
    timeout_seconds="${spec##*:}"
    wait_for_service_health "$ADAPTER_COMPOSE" "$service_name" "$timeout_seconds"
  done
  prepare_adapter_test_artifacts
  wait_for_adapter_client_readiness

  uv run --package "$ADAPTER_PACKAGE" --with pytest --with pandas --with pyarrow \
    pytest "$ADAPTER_TEST_PATH" -m integration --tb=short --verbose

  compose_down "$adapter"
  cleanup_adapter_test_artifacts
  CURRENT_ADAPTER=""
done
