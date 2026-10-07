#!/bin/bash
# ============================================================================
# manage_phone.sh — old name, kept for muscle memory.
#
# All commands now live in the single `organize` program at the project
# root (item 12). This script forces phone mode and forwards to it, so
# existing phone setups keep working. Prefer:  ./organize scan
# ============================================================================
exec "$(cd "$(dirname "$0")" && pwd)/../organize" --phone "$@"
