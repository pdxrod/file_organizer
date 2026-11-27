#!/bin/bash
# file_organizer uninstaller — double-click inside the install folder
# (or run from a terminal). Removes the PATH links, then offers to delete
# the program folder itself.
set -u

DEST="$(cd "$(dirname "$0")" && pwd)"

rm -f /usr/local/bin/organize /opt/homebrew/bin/organize "$HOME/.local/bin/organize"

# Remove exactly the two lines our installer appended (the marker comment
# and the export line that follows it) from each shell startup file.
for RC in "$HOME/.zshrc" "$HOME/.bash_profile"; do
  if grep -q "added by the file_organizer installer" "$RC" 2>/dev/null; then
    TMP="$RC.tmp.$$"
    awk '/added by the file_organizer installer/{skip=2}
         skip==0{print}
         skip>0{skip--}' "$RC" > "$TMP"
    mv "$TMP" "$RC"
  fi
done

echo
echo "✓ 'organize' removed from the PATH."
if [ -t 0 ]; then
  printf "Also delete the program folder %s ? [y/N] " "$DEST"
  read -r ans
  case "$ans" in
    [yY]*) rm -rf "$DEST"; echo "Deleted $DEST" ;;
    *)     echo "Kept at $DEST (delete it by hand whenever you like)." ;;
  esac
else
  echo "To remove the program folder too, delete: $DEST"
fi
