#!/bin/bash
# Test runner script for datus-doris
# Usage: ./scripts/test.sh [unit|integration|acceptance|all]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PACKAGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PACKAGE_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Set test defaults without overriding caller-provided values.
export DORIS_HOST="${DORIS_HOST:-localhost}"
export DORIS_PORT="${DORIS_PORT:-9030}"
export DORIS_USER="${DORIS_USER:-root}"
export DORIS_PASSWORD="${DORIS_PASSWORD:-}"
export DORIS_CATALOG="${DORIS_CATALOG:-internal}"
export DORIS_DATABASE="${DORIS_DATABASE:-test}"

wait_for_doris() {
    uv run python scripts/wait_for_doris.py --timeout "${DORIS_READY_TIMEOUT:-300}"
}

# Function to run tests
run_unit_tests() {
    echo -e "${GREEN}Running unit tests (no database required)...${NC}"
    uv run pytest tests/ -m "not integration" -v
}

run_integration_tests() {
    echo -e "${GREEN}Running integration tests (requires Doris)...${NC}"
    echo -e "${YELLOW}Using: ${DORIS_USER}@${DORIS_HOST}:${DORIS_PORT}/${DORIS_DATABASE}${NC}"
    wait_for_doris
    uv run pytest tests/integration -v
}

run_acceptance_tests() {
    echo -e "${GREEN}Running acceptance tests...${NC}"
    echo -e "${YELLOW}Unit tests:${NC}"
    uv run pytest tests/ -m "acceptance and not integration" -v
    echo -e "\n${YELLOW}Integration tests:${NC}"
    wait_for_doris
    uv run pytest tests/ -m "acceptance and integration" -v
}

run_all_tests() {
    run_unit_tests
    echo ""
    run_integration_tests
}

# Parse command
case "${1:-all}" in
    unit)
        run_unit_tests
        ;;
    integration)
        run_integration_tests
        ;;
    acceptance)
        run_acceptance_tests
        ;;
    all)
        run_all_tests
        ;;
    *)
        echo -e "${RED}Usage: $0 [unit|integration|acceptance|all]${NC}"
        exit 1
        ;;
esac
