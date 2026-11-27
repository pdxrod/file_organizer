#!/bin/bash
# Build the macOS release: dist/file_organizer-mac.zip
#
# The architecture of the machine running this script is the architecture
# of the binary: run on macos-14 (arm64) for Apple Silicon, on macos-13
# (x64) for Intel Macs. The release workflow builds both.
set -euo pipefail
cd "$(dirname "$0")/.."

# Prefer a recent python (the release workflow pins 3.12).
PY=python3
for cand in python3.14 python3.13 python3.12; do
  if command -v "$cand" >/dev/null 2>&1; then PY=$cand; break; fi
done
echo "Using $($PY --version)"

rm -rf build dist
"$PY" -m venv build/venv
build/venv/bin/python -m pip install --quiet --upgrade pip
build/venv/bin/python -m pip install --quiet pyinstaller pyyaml flask

build/venv/bin/python -m PyInstaller \
  --noconfirm --clean --onedir --name organize --console \
  --paths src \
  --collect-submodules file_organizer \
  --collect-submodules flask \
  packaging/computer_entry.py

# Assemble the double-clickable installer folder.
STAGE="dist/file_organizer-mac"
rm -rf "$STAGE"
mkdir -p "$STAGE"
cp -R dist/organize/ "$STAGE/"
cp config_template.yaml "$STAGE/"
cp src/phone_pull.sh "$STAGE/"
cp packaging/install.command packaging/uninstall.command \
   packaging/README-install.txt "$STAGE/"
chmod +x "$STAGE/organize" "$STAGE/install.command" \
          "$STAGE/uninstall.command" "$STAGE/phone_pull.sh"

# Smoke-check the frozen binary before zipping.
"$STAGE/organize" version

( cd dist && ditto -c -k --sequesterRsrc --keepParent \
    file_organizer-mac file_organizer-mac.zip )
echo "Built dist/file_organizer-mac.zip"
