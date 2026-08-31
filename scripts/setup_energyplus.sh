#!/usr/bin/env bash
# Install EnergyPlus (latest stable) into /usr/local/energyplus
# Usage:  bash scripts/setup_energyplus.sh
# Requires: curl, tar (Linux). Windows: use the official installer (docs/windows_setup.md)
set -euo pipefail

echo "[eplus] locating latest EnergyPlus Linux release ..."
# GitHub API follows the redirect from NREL/EnergyPlus -> NatLabRockies/EnergyPlus
TAG=$(curl -sL https://api.github.com/repos/NREL/EnergyPlus/releases/latest \
      | python3 -c "import json,sys; print(json.load(sys.stdin)['tag_name'])")
echo "[eplus] latest release: $TAG"

URL=$(curl -sL https://api.github.com/repos/NREL/EnergyPlus/releases/latest \
      | python3 -c "
import json,sys
d = json.load(sys.stdin)
for a in d['assets']:
    if 'Linux' in a['name'] and 'x86_64' in a['name'] and a['name'].endswith('.tar.gz'):
        print(a['browser_download_url']); break
")
echo "[eplus] downloading $URL ..."
mkdir -p /usr/local/energyplus && cd /usr/local/energyplus
curl -sL -o eplus.tar.gz "$URL"
tar xzf eplus.tar.gz && rm -f eplus.tar.gz
EXE=$(find /usr/local/energyplus -name "energyplus*" \( -type f -o -type l \) | head -1)
"$EXE" --version
echo "[eplus] OK -> $EXE"
echo "        add to PATH:  export PATH=\"$(dirname "$EXE"):\$PATH\""
