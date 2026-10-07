#!/bin/bash
# ============================================================================
# manage_organizer.sh — old name, kept for muscle memory.
#
# All commands now live in the single `organize` program at the project
# root (item 12). This script just forwards to it, so old habits and old
# scripts keep working. Prefer:  ./organize test
# ============================================================================
exec "$(cd "$(dirname "$0")" && pwd)/../organize" "$@"
