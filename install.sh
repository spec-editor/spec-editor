#!/usr/bin/env bash
# ============================================================================
# spec-editor installer — cross-platform, single-command setup
#
# Usage:
#   curl -sSL https://raw.githubusercontent.com/spec-editor/spec-editor/main/install.sh | bash
#
#   # Or with options:
#   curl -sSL https://raw.githubusercontent.com/spec-editor/spec-editor/main/install.sh | bash -s -- --dir ~/my-tools
#
# Options:
#   --dir DIR      Install directory (default: ~/.spec-editor)
#   --python PY    Path to Python 3.11+ binary (default: auto-detect)
#   --branch BR    Git branch for dev install (default: main, implies --dev)
#   --dev          Install from git (editable) instead of PyPI
#   --no-venv      Install into current Python, skip venv creation
#   --version VER  Pin a specific version (default: latest)
#   --help         Show this help
# ============================================================================

set -euo pipefail

# ── Options ────────────────────────────────────────────────────────────────
INSTALL_DIR="${HOME}/.spec-editor"
USE_DEV=false
USE_VENV=true
GIT_BRANCH="main"
VERSION=""
PYTHON_BIN="${PYTHON_BIN:-}"  # Honour env var if set, else auto-detect

while [[ $# -gt 0 ]]; do
    case "$1" in
        --dir)      INSTALL_DIR="$2"; shift 2 ;;
        --branch)   GIT_BRANCH="$2"; USE_DEV=true; shift 2 ;;
        --python)   PYTHON_BIN="$2"; shift 2 ;;
        --dev)      USE_DEV=true; shift ;;
        --no-venv)  USE_VENV=false; shift ;;
        --version)  VERSION="$2"; shift 2 ;;
        --help)
            head -20 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── Colours ────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'
BOLD='\033[1m'; NC='\033[0m'

banner()  { echo -e "${CYAN}${BOLD}==>${NC} ${BOLD}$*${NC}"; }
success() { echo -e "   ${GREEN}✓${NC} $*"; }
warn()    { echo -e "   ${YELLOW}⚠${NC} $*"; }
fail()    { echo -e "   ${RED}✗${NC} $*"; exit 1; }

# ── OS detection ───────────────────────────────────────────────────────────
OS="$(uname -s)"
PYTHON_MIN="3.11"

banner "spec-editor installer"
echo "   OS: ${OS}"

# ── Find Python ────────────────────────────────────────────────────────────
find_python() {
    # Try common Python 3.11+ binary names
    for candidate in python3.13 python3.12 python3.11 python3; do
        local py
        py="$(command -v "${candidate}" 2>/dev/null)" || continue
        local ver
        ver="$("${py}" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null)" || continue
        local major="${ver%%.*}"
        local minor="${ver#*.}"
        if [[ "${major}" -ge 3 && "${minor}" -ge 11 ]] || [[ "${major}" -ge 4 ]]; then
            echo "${py}"
            return 0
        fi
    done
    return 1
}

if [[ -n "${PYTHON_BIN}" ]]; then
    banner "Using specified Python: ${PYTHON_BIN}"
    # Verify the specified Python meets version requirement
    _ver="$("${PYTHON_BIN}" -c 'import sys; print(".".join(map(str, sys.version_info[:2])))' 2>/dev/null)" || fail "Cannot run: ${PYTHON_BIN}"
    _major="${_ver%%.*}"
    _minor="${_ver#*.}"
    if [[ "${_major}" -lt 3 ]] || [[ "${_major}" -eq 3 && "${_minor}" -lt 11 ]]; then
        fail "Python ${PYTHON_MIN}+ required, found: ${_ver} (${PYTHON_BIN})"
    fi
    success "Python: $("${PYTHON_BIN}" --version 2>&1)"
elif found="$(find_python)"; then
    PYTHON_BIN="${found}"
    success "Found Python: $("${PYTHON_BIN}" --version 2>&1)"
else
    echo ""
    echo -e "${RED}${BOLD}Python ${PYTHON_MIN}+ not found.${NC}"
    echo ""
    echo "Install Python first:"
    if [[ "${OS}" == "Darwin" ]]; then
        echo "  brew install python@3.12"
    elif [[ "${OS}" == "Linux" ]]; then
        echo "  sudo apt install python3.12 python3.12-venv    # Ubuntu/Debian"
        echo "  sudo dnf install python3.12                     # Fedora"
    else
        echo "  https://www.python.org/downloads/"
    fi
    echo ""
    exit 1
fi

# ── Install directory ─────────────────────────────────────────────────────
banner "Install directory: ${INSTALL_DIR}"
mkdir -p "${INSTALL_DIR}"

# ── Venv ───────────────────────────────────────────────────────────────────
if [[ "${USE_VENV}" == "true" ]]; then
    VENV_DIR="${INSTALL_DIR}/.venv"
    if [[ -d "${VENV_DIR}" ]]; then
        warn "Virtualenv already exists, reusing: ${VENV_DIR}"
    else
        banner "Creating virtual environment..."
        "${PYTHON_BIN}" -m venv "${VENV_DIR}" || fail "Failed to create venv"
        success "venv created: ${VENV_DIR}"
    fi
    PIP="${VENV_DIR}/bin/pip"
    SPEC_EDITOR="${VENV_DIR}/bin/spec-editor"
else
    PIP="${PYTHON_BIN} -m pip"
    SPEC_EDITOR="$(dirname "${PYTHON_BIN}")/spec-editor"
fi

# ── Install spec-editor ────────────────────────────────────────────────────
banner "Installing spec-editor..."

if [[ "${USE_DEV}" == "true" ]]; then
    # Dev install from git
    REPO_URL="https://github.com/spec-editor/spec-editor.git"
    SRC_DIR="${INSTALL_DIR}/src"

    if [[ -d "${SRC_DIR}/.git" ]]; then
        banner "Pulling latest changes (branch: ${GIT_BRANCH})..."
        (cd "${SRC_DIR}" && git fetch && git checkout "${GIT_BRANCH}" && git pull)
    else
        banner "Cloning repository..."
        git clone --branch "${GIT_BRANCH}" "${REPO_URL}" "${SRC_DIR}"
    fi

    ${PIP} install -e "${SRC_DIR}" || fail "Dev install failed"
    success "Installed spec-editor (dev mode, branch: ${GIT_BRANCH})"
else
    # PyPI install
    PKG="spec-editor"
    [[ -n "${VERSION}" ]] && PKG="spec-editor==${VERSION}"

    ${PIP} install --upgrade "${PKG}" || fail "pip install failed"
    success "Installed spec-editor $( ${SPEC_EDITOR} --version 2>&1 | grep -o '[0-9.]\+' | head -1 )"
fi

# ── PATH setup ─────────────────────────────────────────────────────────────
SHELL_RC=""
case "${SHELL##*/}" in
    zsh)  SHELL_RC="${HOME}/.zshrc" ;;
    bash) SHELL_RC="${HOME}/.bashrc" ;;
    fish) SHELL_RC="${HOME}/.config/fish/config.fish" ;;
esac

BIN_DIR="${INSTALL_DIR}"
if [[ "${USE_VENV}" == "true" ]]; then
    BIN_DIR="${VENV_DIR}/bin"
fi

if [[ -n "${SHELL_RC}" ]]; then
    if ! grep -q "spec-editor" "${SHELL_RC}" 2>/dev/null; then
        echo "" >> "${SHELL_RC}"
        echo "# spec-editor (added by install.sh)" >> "${SHELL_RC}"
        echo "export PATH=\"${BIN_DIR}:\${PATH}\"" >> "${SHELL_RC}"
        success "Added to PATH in ${SHELL_RC}"
    else
        warn "PATH entry already exists in ${SHELL_RC}"
    fi
fi

# ── Symlink (Unix convenience) ─────────────────────────────────────────────
LOCAL_BIN="${HOME}/.local/bin"
if [[ -d "${LOCAL_BIN}" ]]; then
    ln -sf "${BIN_DIR}/spec-editor" "${LOCAL_BIN}/spec-editor" 2>/dev/null || true
    success "Symlinked: ${LOCAL_BIN}/spec-editor"
fi

# ── Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}${BOLD}  spec-editor installed successfully!${NC}"
echo ""
echo "  Binary: ${BIN_DIR}/spec-editor"
echo ""
echo "  Next steps:"
echo "    1. Restart your shell or run:  source ${SHELL_RC:-~/.zshrc}"
echo "    2. Test:                        spec-editor --help"
echo "    3. Start MCP server:           spec-editor mcp -p /path/to/project"
echo ""
echo "  Documentation: https://github.com/spec-editor/spec-editor"
echo -e "${GREEN}${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
