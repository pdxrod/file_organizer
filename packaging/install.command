#!/bin/bash
# file_organizer installer — double-click this after unzipping.
# Installs into ~/Applications/file_organizer and puts `organize` on the
# PATH. Safe to run again (it replaces the previous install).
#
# Testing hooks (not for users):
#   FILE_ORGANIZER_INSTALL_DIR=...  install somewhere else
#   FILE_ORGANIZER_NO_LINK=1        do not touch the PATH
set -u

SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${FILE_ORGANIZER_INSTALL_DIR:-$HOME/Applications/file_organizer}"

mkdir -p "$HOME/Applications"
if [ -e "$DEST" ] && [ "$DEST" != "$SRC" ]; then
  rm -rf "$DEST"
fi
if [ "$DEST" != "$SRC" ]; then
  ditto "$SRC" "$DEST"
fi
chmod +x "$DEST/organize"
# The zip carries com.apple.quarantine; strip it so the installed binary
# and scripts run without Gatekeeper prompts (we just installed them on
# purpose — this is the user's own Applications folder).
xattr -dr com.apple.quarantine "$DEST" 2>/dev/null || true

if [ "${FILE_ORGANIZER_NO_LINK:-0}" = "1" ]; then
  LINK_DIR=""
else
  LINK_DIR=""
  if [ -w /usr/local/bin ] || { [ ! -e /usr/local/bin ] && mkdir -p /usr/local/bin 2>/dev/null; }; then
    ln -sf "$DEST/organize" /usr/local/bin/organize && LINK_DIR="/usr/local/bin"
  elif [ -d /opt/homebrew/bin ] && [ -w /opt/homebrew/bin ]; then
    ln -sf "$DEST/organize" /opt/homebrew/bin/organize && LINK_DIR="/opt/homebrew/bin"
  else
    mkdir -p "$HOME/.local/bin"
    ln -sf "$DEST/organize" "$HOME/.local/bin/organize" && LINK_DIR="$HOME/.local/bin"
    for RC in "$HOME/.zshrc" "$HOME/.bash_profile"; do
      touch "$RC"
      if ! grep -q "file_organizer" "$RC" 2>/dev/null; then
        printf '\n# added by the file_organizer installer\nexport PATH="%s:$PATH"\n' \
          "$HOME/.local/bin" >> "$RC"
      fi
    done
  fi
fi

echo
echo "✓ file_organizer is installed in $DEST"
if [ -n "$LINK_DIR" ]; then
  echo "  'organize' is linked into $LINK_DIR"
fi
echo "  Open a NEW Terminal window and type:  organize"
echo "  The first run walks you through the settings."
echo
echo "If macOS blocks anything with 'cannot be opened because the"
echo "developer cannot be verified': right-click it in Finder → Open → Open."
echo "Remove it later by double-clicking uninstall.command in the install folder."
