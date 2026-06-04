#!/usr/bin/env bash
set -euo pipefail

VENV_DIR="venv"
REQUIREMENTS="requirements.txt"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$PROJECT_ROOT"

# ── Colors ────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

info()    { echo -e "${CYAN}[info]${RESET} $*"; }
success() { echo -e "${GREEN}[ok]${RESET}   $*"; }
warn()    { echo -e "${YELLOW}[warn]${RESET} $*"; }
error()   { echo -e "${RED}[err]${RESET}  $*" >&2; }

# ── Helpers ───────────────────────────────────────────────────────────────────
ensure_venv() {
    if [[ ! -d "$VENV_DIR" ]]; then
        warn "Virtualenv not found – creating '$VENV_DIR'..."
        python3 -m venv "$VENV_DIR"
        success "Virtualenv created."
    fi
}

activate_venv() {
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"
}

needs_install() {
    "$VENV_DIR/bin/python" -c "import fastapi, dotenv" 2>/dev/null && return 1 || return 0
}

do_install() {
    ensure_venv
    activate_venv
    info "Installing dependencies from $REQUIREMENTS..."
    pip install --quiet --upgrade pip
    pip install --quiet -r "$REQUIREMENTS"
    success "Dependencies installed."
}

ensure_deps() {
    ensure_venv
    if needs_install; then
        warn "Dependencies not installed – running install..."
        do_install
    else
        activate_venv
    fi
}

# ── Commands ──────────────────────────────────────────────────────────────────
cmd_install() {
    do_install
}

cmd_start() {
    ensure_deps
    info "Starting Plannie"
    echo -e "${BOLD}  Stop the service: Ctrl+C${RESET}"
    echo ""
    exec "$VENV_DIR/bin/python" -m src.main
}

cmd_test() {
    ensure_deps
    info "Running tests..."
    echo ""
    "$VENV_DIR/bin/python" -m pytest tests/ -v "$@"
with:
    "$VENV_DIR/bin/python" -m pytest tests/ -v "$@"
}

# ── Help ──────────────────────────────────────────────────────────────────────
usage() {
    echo ""
    echo -e "${BOLD}Plannie – launcher${RESET}"
    echo ""
    echo -e "  ${CYAN}./run.sh${RESET} ${BOLD}<command>${RESET}"
    echo ""
    echo -e "  ${BOLD}Commands:${RESET}"
    echo -e "    ${GREEN}start${RESET}     Start the service (default http://0.0.0.0:8000)"
    echo -e "    ${GREEN}test${RESET}      Run tests (extra args are forwarded to pytest)"
    echo -e "    ${GREEN}install${RESET}   Create venv and install dependencies"
    echo ""
    echo -e "  ${BOLD}Environment variables:${RESET}"
    echo -e "    HOST   Listening address (default: 0.0.0.0)"
    echo -e "    PORT   Listening port    (default: 8000)"
    echo ""
    echo -e "  ${BOLD}Examples:${RESET}"
    echo -e "    ${YELLOW}./run.sh start${RESET}"
    echo -e "    ${YELLOW}PORT=9000 ./run.sh start${RESET}"
    echo -e "    ${YELLOW}./run.sh test${RESET}"
    echo -e "    ${YELLOW}./run.sh test -k test_voting${RESET}"
    echo -e "    ${YELLOW}./run.sh install${RESET}"
    echo ""
}

# ── Dispatcher ────────────────────────────────────────────────────────────────
COMMAND="${1:-}"
shift || true

case "$COMMAND" in
    start)   cmd_start "$@" ;;
    test)    cmd_test  "$@" ;;
    install) cmd_install ;;
    "")      usage ;;
    *)
        error "Unknown command: '$COMMAND'"
        usage
        exit 1
        ;;
esac
