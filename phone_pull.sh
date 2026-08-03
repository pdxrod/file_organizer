#!/bin/bash
# Fast pull: streams phone media to ~/PhoneMedia via tar pipe
ADB="$HOME/Library/Android/sdk/platform-tools/adb"
PHONE="R5GL11RL4ML"
LOCAL="$HOME/PhoneMedia"
mkdir -p "$LOCAL"

echo "Pulling from phone (streaming)..."
"$ADB" -s "$PHONE" exec-out "cd /storage/emulated/0 && tar cf - DCIM/Camera DCIM/Screenshots Pictures 2>/dev/null" | tar xf - -C "$LOCAL/" 2>/dev/null
echo "Done. $(find "$LOCAL" -type f | wc -l) files in $LOCAL"
