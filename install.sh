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

if [ "${1-}" = "ci" ] || [ $# -gt 0 ]; then
  echo "Skipping pip3 install (CI or manual skip)."
  print_versions
  exit 0
fi

print_versions
pip3 install --no-input -r requirements.txt --user --no-warn-script-location --upgrade
