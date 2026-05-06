#!/usr/bin/env bash
# Install Python dependencies for this Node Server on the Polyglot/eisy host.
# Usage: ./install.sh          # normal: pip install (does not upgrade pip in-place)
#        ./install.sh ci       # skip pip (e.g. CI)

set -euo pipefail
cd "$(dirname "$0")"

print_versions() {
  echo "---- install.sh versions ----"
  command -v python3 >/dev/null 2>&1 && echo "python3: $(python3 -V 2>&1)" || echo "python3: (not found)"
  command -v pip3 >/dev/null 2>&1 && echo "pip3: $(pip3 -V 2>&1)" || echo "pip3: (not found)"
  if python3 -c "import udi_interface" 2>/dev/null; then
    python3 -c "import udi_interface as u; print('udi_interface:', getattr(u, '__version__', 'unknown'))"
  else
    echo "udi_interface: (not importable yet — normal on first install before requirements.txt)"
  fi
  echo "-----------------------------"
}

ensure_orjson_freebsd() {
  # FreeBSD + Python 3.11 can fail building orjson from source (maturin/rust path).
  # Prefer the OS package when available so pip can continue with pure-Python deps.
  if ! command -v freebsd-version >/dev/null 2>&1; then
    return 0
  fi
  if python3 -c "import orjson" 2>/dev/null; then
    echo "orjson: already importable"
    return 0
  fi
  if ! command -v pkg >/dev/null 2>&1; then
    echo "WARNING: FreeBSD detected but pkg is unavailable; orjson may fail to build from source."
    return 0
  fi

  py_tag="$(python3 -c 'import sys; print(f"py{sys.version_info[0]}{sys.version_info[1]}")')"
  orjson_pkg="${py_tag}-orjson"

  if pkg info "${orjson_pkg}" >/dev/null 2>&1; then
    echo "orjson package already installed: ${orjson_pkg}"
    return 0
  fi

  echo "FreeBSD preflight: installing ${orjson_pkg} to avoid pip source-build failures..."
  if [ "$(id -u)" -eq 0 ]; then
    pkg install -y "${orjson_pkg}"
  elif command -v sudo >/dev/null 2>&1 && sudo -n pkg install -y "${orjson_pkg}" 2>/dev/null; then
    : # Non-interactive sudo (e.g. NOPASSWD) — typical for automation; PG3 has no TTY for a password.
  else
    echo "WARNING: Could not install OS package ${orjson_pkg} automatically."
    echo "  PG3/eisy installs often run without a TTY, so interactive sudo cannot prompt for a password."
    echo "  Fix one of:"
    echo "    - As root once: pkg install -y ${orjson_pkg}"
    echo "    - Or configure passwordless sudo for pkg(8) for the Polyglot user"
    echo "  Continuing with pip; orjson may still install from a wheel or may fail to build from source."
    return 0
  fi
}

if [ "${1-}" = "ci" ] || [ $# -gt 0 ]; then
  echo "Skipping pip3 install (CI or manual skip)."
  print_versions
  exit 0
fi

print_versions
ensure_orjson_freebsd
pip3 install --no-input -r requirements.txt --user --no-warn-script-location --upgrade
