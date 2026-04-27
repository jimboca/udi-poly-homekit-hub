#!/usr/bin/env bash
# Install Python dependencies for this Node Server on the Polyglot/eisy host.
# Usage: ./install.sh          # normal: pip install
#        ./install.sh ci       # skip pip (e.g. CI)

set -e
cd "$(dirname "$0")"

if [ $# -gt 0 ]; then
  echo "Skipping pip3 install (CI or manual skip)."
else
  pip3 install --upgrade pip
  pip3 install -r requirements.txt --user --no-warn-script-location --upgrade
fi
