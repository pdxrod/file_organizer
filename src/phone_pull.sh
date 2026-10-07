#!/bin/bash
# =============================================================================
# phone_pull.sh — bidirectional phone sync via ADB
# =============================================================================
# Pulls files from Android phones to the Mac (default), or pushes from Mac to
# the phone. Designed as a companion to phone_sync.py for when phones are
# connected via USB.
#
# Usage:
#   ./phone_pull.sh                  # pull from all connected phones (mapped
#                                    # to MAIN_DRIVE folders, item 12b)
#   ./phone_pull.sh --push           # push: remote_stage → phone
#   ./phone_pull.sh --device R5GL11RL4ML  # use specific device
#   ./phone_pull.sh --list           # list connected devices
#   ./phone_pull.sh --dry-run        # show what would be synced
#   ./phone_pull.sh --help           # this message
#
# Requirements:
#   - ADB installed (Android SDK platform-tools)
#   - Phone connected via USB with USB debugging enabled
#   - rsync (for efficient file comparison)
#
# Configuration:
#   Edit the variables below, or create ~/.phone_pull.conf
# =============================================================================

set -euo pipefail

# ── Configuration (from config.yaml) ─────────────────────────────────────────
# All paths are read from the file_organizer config.yaml so nothing is
# hardcoded.  Works with Proton Drive, Google Drive, external drives, etc.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# config.yaml lives at the project root (next to organize); fall back to
# this script's directory when run from an old checkout.
if [ -f "$(dirname "$SCRIPT_DIR")/config.yaml" ]; then
    CONFIG_YAML="$(dirname "$SCRIPT_DIR")/config.yaml"
else
    CONFIG_YAML="${SCRIPT_DIR}/config.yaml"
fi

# Parse config.yaml with python3 — resolves drive: references like MAIN_DRIVE
_load_config() {
    python3 <<PYEOF
import sys, os, yaml, shlex
cfg = yaml.safe_load(open('$CONFIG_YAML')) or {}
# Since the single-template merge (item 5), config.yaml is generated from
# the computer: section — accept both sectioned and legacy flat formats.
if isinstance(cfg, dict) and isinstance(cfg.get('computer'), dict):
    cfg = cfg['computer']
drives = cfg.get('drives', {})

def resolve(path):
    path = os.path.expanduser(path)
    for drive, base in drives.items():
        base = os.path.expanduser(base)
        if path.startswith(drive + '/'):
            return path.replace(drive, base, 1)
        if path.startswith(drive):
            # edge: bare drive key like 'MAIN_DRIVE'
            return base + path[len(drive):]
    return path

pp = cfg.get('phone_pull', {}) or {}
defaults = {
    'local_stage': '~/misc',
    'remote_stage': '~/misc',
    'adb_path': 'adb',
    'phone_source_dirs': ['DCIM/Camera', 'DCIM/Screenshots', 'Pictures',
                          'Download', 'Documents', 'Movies', 'Music'],
    'phone_push_dir': 'ProtonDrive/My Files/misc',
    'android_base': '/storage/emulated/0',
    'min_age_minutes': 5,
    'state_file': '~/.phone_pull_state',
    'include_extensions': [],
    'exclude_extensions': ['.tmp', '.temp', '.partial', '.crdownload', '.part', '.download', '.crypt14', '.pyc', '.log'],
}
local_stage = resolve(pp.get('local_stage') or defaults['local_stage'])
remote_stage = resolve(pp.get('remote_stage') or defaults['remote_stage'])

# Fail fast if the config still contains unedited template placeholders
# (item 7). Real missing paths are fine (drives come and go); placeholder
# paths mean config.yaml was never personalised.
markers = ('janedoe', 'yourexternaldrive', 'yourgoogledrive', 'yourname')
problems = []
for label, p in [('local_stage', local_stage), ('remote_stage', remote_stage)]:
    if not os.path.exists(os.path.expanduser(p)) and any(
        m in p.lower() for m in markers
    ):
        problems.append(f'{label}: {p}')
if problems:
    sys.stderr.write(
        'ERROR: your config.yaml still contains template placeholder paths '
        'that do not exist:\n  ' + '\n  '.join(problems) +
        '\n\nEdit config.yaml and set your real paths, then run again.\n'
    )
    sys.exit(1)

print('LOCAL_STAGE=' + shlex.quote(local_stage))
print('REMOTE_STAGE=' + shlex.quote(remote_stage))
print('ADB=' + shlex.quote(resolve(pp.get('adb_path') or defaults['adb_path'])))
# Temp staging lives inside local_stage; relative values are joined onto it.
ts = pp.get('temp_stage')
if not ts:
    ts = os.path.join(local_stage, '.tmp_pull')
elif not os.path.isabs(ts):
    ts = os.path.join(local_stage, ts)
print('TEMP_STAGE=' + shlex.quote(ts))
print('ANDROID_BASE=' + shlex.quote(pp.get('android_base') or defaults['android_base']))
try:
    min_age = int(pp.get('min_age_minutes', defaults['min_age_minutes']))
except (TypeError, ValueError):
    min_age = defaults['min_age_minutes']
print('MIN_AGE_MINUTES=' + shlex.quote(str(min_age)))
print('STATE_FILE=' + shlex.quote(os.path.expanduser(pp.get('state_file') or defaults['state_file'])))
src_dirs = pp.get('phone_source_dirs') or defaults['phone_source_dirs']
print('PHONE_SOURCE_DIRS=(' + ' '.join(shlex.quote(d) for d in src_dirs) + ')')
print('PHONE_TARGET_DIR=' + shlex.quote(pp.get('phone_push_dir') or defaults['phone_push_dir']))
print('INCLUDE_EXTS=(' + ' '.join(shlex.quote(str(e).lower()) for e in (pp.get('include_extensions') or defaults['include_extensions'])) + ')')
print('EXCLUDE_EXTS=(' + ' '.join(shlex.quote(str(e).lower()) for e in (pp.get('exclude_extensions') or defaults['exclude_extensions'])) + ')')

# Item 12b: staging subfolder → computer folder mapping.
main_drive = resolve(drives.get('MAIN_DRIVE', '')) if drives.get('MAIN_DRIVE') else ''
print('MAIN_DRIVE=' + shlex.quote(main_drive))
misc_dir = os.path.join(main_drive, 'misc') if main_drive else local_stage
print('MISC_DIR=' + shlex.quote(misc_dir))
staging_map = pp.get('staging_map') or {}
tsv_lines = []
for prefix, dest in staging_map.items():
    resolved = resolve(str(dest))
    tsv_lines.append(f'{prefix}\t{resolved}')
print('STAGING_MAP_TSV=' + shlex.quote('\n'.join(tsv_lines)))

# Item 13: hold app-generated names until renamed; normalise leading dates.
hold = pp.get('hold_app_generated_names', True)
normalise = pp.get('normalise_leading_dates', True)
print('HOLD_APP_GENERATED=' + ('true' if hold else 'false'))
print('NORMALISE_DATES=' + ('true' if normalise else 'false'))
PYEOF
}

eval "$(_load_config)"

# Fallbacks if config.yaml is missing these (loader usually supplies them)
: "${TEMP_STAGE:=${LOCAL_STAGE}/.tmp_pull}"
: "${ANDROID_BASE:=/storage/emulated/0}"
: "${MIN_AGE_MINUTES:=5}"
: "${STATE_FILE:=${HOME}/.phone_pull_state}"
: "${MAIN_DRIVE:=}"
: "${MISC_DIR:=${LOCAL_STAGE}}"
: "${STAGING_MAP_TSV:=}"
: "${HOLD_APP_GENERATED:=true}"
: "${NORMALISE_DATES:=true}"

# Item 13 helpers — reuse the shared filename rules from the package.
PY_SRC="$(dirname "$CONFIG_YAML")/src"

name_is_app_generated() {
    PYTHONPATH="$PY_SRC" python3 - "$1" <<'PYEOF'
import sys
try:
    from file_organizer.filename_rules import is_app_generated
    print("yes" if is_app_generated(sys.argv[1]) else "no")
except Exception:
    print("no")
PYEOF
}

normalise_name() {
    PYTHONPATH="$PY_SRC" python3 - "$1" <<'PYEOF'
import sys
try:
    from file_organizer.filename_rules import normalise_leading_date
    print(normalise_leading_date(sys.argv[1]))
except Exception:
    print(sys.argv[1])
PYEOF
}

# Item 12b: map a staging-relative directory to its computer folder.
# Prints "<dest_dir>\t<remainder>". Rules, in order:
#   1. longest configured staging_map prefix (e.g. "Music" → "MAIN_DRIVE/Entertainment")
#   2. longest prefix whose same-named folder exists under MAIN_DRIVE
#   3. MISC_DIR (created on demand), full staging path preserved under it
map_dest_root() {
    local rel="$1" best="" best_dest="" prefix mapped rem
    if [[ -n "$STAGING_MAP_TSV" ]]; then
        while IFS=$'\t' read -r prefix mapped; do
            [[ -z "$prefix" ]] && continue
            if [[ "$rel" == "$prefix" || "$rel" == "$prefix"/* ]]; then
                if [[ ${#prefix} -gt ${#best} ]]; then
                    best="$prefix"
                    best_dest="$mapped"
                fi
            fi
        done <<< "$STAGING_MAP_TSV"
        if [[ -n "$best" ]]; then
            rem="${rel#$best}"
            rem="${rem#/}"
            printf '%s\t%s\n' "$best_dest" "$rem"
            return 0
        fi
    fi
    if [[ -n "$MAIN_DRIVE" ]]; then
        local cur="$rel" parts i
        while [[ -n "$cur" ]]; do
            if [[ -d "$MAIN_DRIVE/$cur" ]]; then
                rem="${rel#$cur}"
                rem="${rem#/}"
                printf '%s\t%s\n' "$MAIN_DRIVE/$cur" "$rem"
                return 0
            fi
            [[ "$cur" == */* ]] || break
            cur="${cur%/*}"
        done
    fi
    printf '%s\t%s\n' "$MISC_DIR" "$rel"
    return 0
}

# ── Help ─────────────────────────────────────────────────────────────────────

show_help() {
    cat << 'EOF'
phone_pull.sh — bidirectional phone sync via ADB

USAGE:
  ./phone_pull.sh [OPTIONS]

OPTIONS:
  --help           Show this message
  --list           List connected ADB devices
  --device ID      Use a specific device (default: all connected)
  --push           Push mode: copy files from remote_stage TO the phone
  --dry-run        Show what would be synced without copying
  --verbose        Verbose output

DESCRIPTION:
  Pull mode (default): Copies new/changed files from the phone's
  file_organizer_staging folders onto this computer, KEEPING the staging
  subfolder structure (item 12b). Each staging subfolder is mapped to a
  computer folder: a configured phone_pull.staging_map entry first, then
  a same-named folder under MAIN_DRIVE when it exists, otherwise
  MAIN_DRIVE/misc (created on demand). The file_organizer daemon then
  syncs those folders to your cloud drive.

  Push mode (--push): Copies files from remote_stage back to the phone.

  Both modes track file modification times to avoid re-copying unchanged
  files. Files younger than MIN_AGE_MINUTES are skipped to give you time
  to delete unwanted files before they are synced. Existing files are
  overwritten only when the incoming file is newer AND a different size —
  no _1/_N renames (item 10).

CONFIGURATION:
  All settings come from config.yaml — see the phone_pull: section.
  - local_stage:  fallback landing folder when MAIN_DRIVE is unset
  - remote_stage: cloud-drive folder for push mode (e.g. Proton Drive)
  - staging_map:  staging subfolder → computer folder overrides (item 12b)
  - adb_path:     path to the adb binary
  - temp_stage:   temp folder used during pulls (default: <local_stage>/.tmp_pull)
  - android_base: phone shared-storage root (default: /storage/emulated/0)
  - min_age_minutes: skip files younger than this many minutes
  - state_file:   file tracking last-synced mtimes
  - include_extensions / exclude_extensions: extension filters
EOF
}

# ── Logging ──────────────────────────────────────────────────────────────────

log()  { echo "$(date '+%Y-%m-%d %H:%M:%S') [phone_pull] $*"; }
info() { log "INFO  $*"; }
warn() { log "WARN  $*" >&2; }
err()  { log "ERROR $*" >&2; }
dbg()  { [[ "${VERBOSE:-false}" == "true" ]] && log "DEBUG $*" || true; }

# ── Device management ────────────────────────────────────────────────────────

list_devices() {
    local devices
    devices=$("$ADB" devices 2>/dev/null | tail -n +2 | grep -v '^$' | awk '{print $1}')
    if [[ -z "$devices" ]]; then
        echo "No devices connected."
        echo ""
        echo "Troubleshooting:"
        echo "  1. Connect phone via USB"
        echo "  2. Enable USB debugging (Developer Options)"
        echo "  3. Accept the RSA key fingerprint on the phone"
        echo "  4. Run 'adb devices' to verify"
        return 1
    fi
    echo "Connected devices:"
    "$ADB" devices -l 2>/dev/null | tail -n +2 | grep -v '^$'
}

# ── State tracking ───────────────────────────────────────────────────────────

load_state() {
    [[ -f "$STATE_FILE" ]] && cat "$STATE_FILE" || echo "{}"
}

save_state() {
    echo "$1" > "$STATE_FILE"
}

get_mtime_key() {
    # Create a unique key for a device+file combination
    echo "${1}::${2}"
}

# ── Age check ────────────────────────────────────────────────────────────────

is_old_enough() {
    local device_id="$1"
    local phone_path="$2"
    local min_age_sec=$((MIN_AGE_MINUTES * 60))

    local mtime
    mtime=$("$ADB" -s "$device_id" shell "stat -c %Y '$phone_path' 2>/dev/null" 2>/dev/null | tr -d '\r\n' || echo "0")
    local now
    now=$("$ADB" -s "$device_id" shell "date +%s 2>/dev/null" 2>/dev/null | tr -d '\r\n' || echo "0")

    if [[ "$mtime" == "0" || "$now" == "0" ]]; then
        return 0  # can't determine age — allow it
    fi

    local age=$((now - mtime))
    if [[ $age -lt $min_age_sec ]]; then
        dbg "Skipping (too new, ${age}s old): $phone_path"
        return 1
    fi
    return 0
}

# ── Pull mode ────────────────────────────────────────────────────────────────

do_pull() {
    local device_id="$1"
    local dry_run="${2:-false}"

    local state
    state=$(load_state)
    local total_copied=0
    local total_skipped=0
    local total_bytes=0

    info "Pulling from device: $device_id"

    # Get device name for display
    local device_name
    device_name=$("$ADB" -s "$device_id" shell "getprop ro.product.model" 2>/dev/null | tr -d '\r\n' || echo "$device_id")
    info "Device model: $device_name"

    # Ensure target dir exists
    mkdir -p "$LOCAL_STAGE"

    # Build list of source dirs that exist on phone
    local valid_dirs=()
    for src_dir in "${PHONE_SOURCE_DIRS[@]}"; do
        local phone_dir="${ANDROID_BASE}/${src_dir}"
        local dir_exists
        dir_exists=$("$ADB" -s "$device_id" shell "[ -d '$phone_dir' ] && echo yes || echo no" 2>/dev/null | tr -d '\r\n')
        if [[ "$dir_exists" == "yes" ]]; then
            valid_dirs+=("$phone_dir")
        else
            dbg "Directory not found on phone: $phone_dir"
        fi
    done

    if [[ ${#valid_dirs[@]} -eq 0 ]]; then
        warn "No source directories found on device $device_id"
        return 0
    fi

    # ── Dry-run: list files without copying ──
    if [[ "$dry_run" == "true" ]]; then
        info "[DRY-RUN] Would pull from: ${valid_dirs[*]}"
        for phone_dir in "${valid_dirs[@]}"; do
            "$ADB" -s "$device_id" shell "find '$phone_dir' -type f 2>/dev/null" 2>/dev/null | while IFS= read -r f; do
                [[ -z "$f" ]] && continue
                local fname rel rel_dir mapped held dest_fname
                fname=$(basename "$f")
                rel="${f#$ANDROID_BASE/}"
                rel_dir="${rel%/*}"
                [[ "$rel_dir" == "$rel" ]] && rel_dir=""
                if [[ "$HOLD_APP_GENERATED" == "true" ]]; then
                    held=$(name_is_app_generated "$fname")
                    if [[ "$held" == "yes" ]]; then
                        info "[DRY-RUN]   HOLD until renamed: $f"
                        continue
                    fi
                fi
                dest_fname="$fname"
                if [[ "$NORMALISE_DATES" == "true" ]]; then
                    dest_fname=$(normalise_name "$fname")
                fi
                mapped=$(map_dest_root "$rel_dir")
                info "[DRY-RUN]   $f → ${mapped%%$'\t'*}/$dest_fname"
            done
        done
        return 0
    fi

    # ── Phase 1: Stream from phone to temp staging (preserves structure) ──
    info "Phase 1: Streaming from phone to temp staging..."
    rm -rf "$TEMP_STAGE"
    mkdir -p "$TEMP_STAGE"

    # Build relative paths for tar (relative to ANDROID_BASE)
    local tar_args=""
    for phone_dir in "${valid_dirs[@]}"; do
        local rel="${phone_dir#$ANDROID_BASE/}"
        tar_args="$tar_args '$rel'"
    done

    # Use tar pipe for speed: adb exec-out streams tar, local tar extracts
    eval "\"$ADB\" -s \"$device_id\" exec-out \"cd $ANDROID_BASE && tar cf - $tar_args 2>/dev/null\" 2>/dev/null | tar xf - -C \"$TEMP_STAGE\" 2>/dev/null" || true

    local pulled_count
    pulled_count=$(find "$TEMP_STAGE" -type f 2>/dev/null | wc -l | tr -d ' ')
    info "Phase 1 done: $pulled_count files in temp staging"

    if [[ "$pulled_count" -eq 0 ]]; then
        info "No files to process."
        rm -rf "$TEMP_STAGE"
        return 0
    fi

    # ── Phase 2: Map staging subfolders to computer folders (item 12b) ──
    info "Phase 2: Mapping staging subfolders to computer folders..."

    local min_age_sec=$((MIN_AGE_MINUTES * 60))
    local now_sec
    now_sec=$(date +%s)

    # Detect stat flavour (BSD/macOS vs GNU/Linux)
    local stat_mtime_flag stat_size_flag
    if stat -f %m "$TEMP_STAGE" 2>/dev/null >/dev/null; then
        stat_mtime_flag="-f %m"
        stat_size_flag="-f %z"
    else
        stat_mtime_flag="-c %Y"
        stat_size_flag="-c %s"
    fi

    while IFS= read -r -d '' temp_file; do
        local fname rel_path phone_path
        fname=$(basename "$temp_file")
        rel_path="${temp_file#$TEMP_STAGE/}"
        phone_path="${ANDROID_BASE}/${rel_path}"

        # Extension filter (case-insensitive; Bash 3.2 has no ${var,,})
        local ext=".${fname##*.}"
        [[ "$ext" == ".$fname" ]] && ext=""
        ext=$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')
        local skip=false
        if [[ ${#EXCLUDE_EXTS[@]} -gt 0 ]]; then
            for ex in "${EXCLUDE_EXTS[@]}"; do
                [[ "$ext" == "$ex" ]] && skip=true && break
            done
        fi
        [[ "$skip" == "true" ]] && continue

        if [[ ${#INCLUDE_EXTS[@]} -gt 0 ]]; then
            skip=true
            for inc in "${INCLUDE_EXTS[@]}"; do
                [[ "$ext" == "$inc" ]] && skip=false && break
            done
            [[ "$skip" == "true" ]] && continue
        fi

        # Age check (tar preserves mtime, so local stat is fine)
        local file_mtime
        file_mtime=$(stat $stat_mtime_flag "$temp_file" 2>/dev/null || echo "0")
        local file_age=$((now_sec - file_mtime))
        if [[ $file_age -lt $min_age_sec ]]; then
            dbg "Skipping (too new, ${file_age}s old): $fname"
            continue
        fi

        # State check (skip if mtime hasn't changed since last pull)
        local state_key="${device_id}::${phone_path}"
        local last_mtime
        last_mtime=$(echo "$state" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('$state_key', 0))" 2>/dev/null || echo "0")

        if [[ "$file_mtime" == "$last_mtime" ]]; then
            ((total_skipped++)) || true
            dbg "Skipping (unchanged): $fname"
            continue
        fi

        # Get file size for reporting
        local file_size
        file_size=$(stat $stat_size_flag "$temp_file" 2>/dev/null || echo "0")

        # Item 12b: keep the staging subfolder structure and map it to the
        # computer folder (configured mapping → same-name MAIN_DRIVE folder
        # → MAIN_DRIVE/misc fallback, created on demand).
        local rel_dir="${rel_path%/*}"
        [[ "$rel_dir" == "$rel_path" ]] && rel_dir=""
        local mapped dest_root dest_rel dest
        mapped=$(map_dest_root "$rel_dir")
        dest_root="${mapped%%$'\t'*}"
        dest_rel="${mapped#*$'\t'}"
        if [[ -n "$dest_rel" ]]; then
            dest="$dest_root/$dest_rel/$fname"
        else
            dest="$dest_root/$fname"
        fi

        # Item 10: never invent _1/_N names. If a file is already there,
        # overwrite only when the incoming one is newer AND a different
        # size; otherwise keep what is there.
        if [[ -f "$dest" ]]; then
            local dest_mtime dest_size
            dest_mtime=$(stat $stat_mtime_flag "$dest" 2>/dev/null || echo "0")
            dest_size=$(stat $stat_size_flag "$dest" 2>/dev/null || echo "0")
            if [[ "$file_mtime" -le "$dest_mtime" || "$file_size" == "$dest_size" ]]; then
                dbg "Keeping existing (not newer+different): $dest"
                continue
            fi
            dbg "Overwriting (newer, different size): $dest"
        fi

        mkdir -p "$(dirname "$dest")"
        if mv "$temp_file" "$dest" 2>/dev/null; then
            ((total_copied++)) || true
            total_bytes=$((total_bytes + file_size))
            info "  $rel_path → $dest ($(( file_size / 1024 )) KB)"

            # Update state
            state=$(echo "$state" | python3 -c "import sys,json; d=json.load(sys.stdin); d['$state_key']='$file_mtime'; print(json.dumps(d))" 2>/dev/null || echo "$state")
        else
            err "Failed to move: $fname"
        fi
    done < <(find "$TEMP_STAGE" -type f -print0 2>/dev/null)

    # ── Phase 3: Clean up temp staging ──
    rm -rf "$TEMP_STAGE"
    info "Phase 3: Temp staging cleaned up"

    # Save state
    save_state "$state"

    info "Pull complete for $device_name: copied=$total_copied skipped=$total_skipped bytes=$total_bytes"
    return 0
}

# ── Push mode ────────────────────────────────────────────────────────────────

do_push() {
    local device_id="$1"
    local dry_run="${2:-false}"

    info "Pushing to device: $device_id"

    # Check that remote stage exists
    if [[ ! -d "$REMOTE_STAGE" ]]; then
        err "Remote stage folder not found: $REMOTE_STAGE"
        err "Check phone_pull.remote_stage in config.yaml"
        return 1
    fi

    local phone_target="${ANDROID_BASE}/${PHONE_TARGET_DIR}"

    # Ensure target directory exists on phone
    "$ADB" -s "$device_id" shell "mkdir -p '$phone_target'" 2>/dev/null || true

    local total_pushed=0

    # Walk files in remote stage and push newer ones
    while IFS= read -r -d '' local_file; do
        local rel_path="${local_file#$REMOTE_STAGE/}"
        local phone_path="${phone_target}/${rel_path}"

        # Extension filter for push (case-insensitive; Bash 3.2 compatible)
        local ext=".${local_file##*.}"
        [[ "$ext" == ".$local_file" ]] && ext=""
        ext=$(printf '%s' "$ext" | tr '[:upper:]' '[:lower:]')
        if [[ ${#EXCLUDE_EXTS[@]} -gt 0 ]]; then
            for ex in "${EXCLUDE_EXTS[@]}"; do
                [[ "$ext" == "$ex" ]] && continue 2
            done
        fi

        if [[ "$dry_run" == "true" ]]; then
            info "[DRY-RUN] Would push: $local_file → $phone_path"
            continue
        fi

        # Push the file
        local phone_dir
        phone_dir=$(dirname "$phone_path")
        "$ADB" -s "$device_id" shell "mkdir -p '$phone_dir'" 2>/dev/null || true

        if "$ADB" -s "$device_id" push "$local_file" "$phone_path" 2>/dev/null; then
            ((total_pushed++)) || true
            info "Pushed: $local_file → $phone_path"
        else
            err "Failed to push: $local_file"
        fi
    done < <(find "$REMOTE_STAGE" -type f -print0 2>/dev/null)

    info "Push complete: pushed=$total_pushed files"
    return 0
}

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    local mode="pull"
    local device_id=""
    local dry_run="false"
    VERBOSE="false"

    # Parse arguments
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --help|-h)
                show_help
                exit 0
                ;;
            --list)
                list_devices
                exit $?
                ;;
            --push)
                mode="push"
                shift
                ;;
            --device)
                device_id="$2"
                shift 2
                ;;
            --dry-run)
                dry_run="true"
                shift
                ;;
            --verbose|-v)
                VERBOSE="true"
                shift
                ;;
            *)
                err "Unknown option: $1"
                echo "Use --help for usage."
                exit 1
                ;;
        esac
    done

    # Verify ADB is available
    if [[ ! -x "$ADB" ]]; then
        # Try to find adb in PATH
        if command -v adb >/dev/null 2>&1; then
            ADB="adb"
        else
            err "ADB not found at: $ADB"
            err "Set phone_pull.adb_path in config.yaml, or install:"
            err "  brew install android-platform-tools   # macOS"
            exit 1
        fi
    fi

    # Get device list
    local devices
    if [[ -n "$device_id" ]]; then
        devices="$device_id"
    else
        devices=$("$ADB" devices 2>/dev/null | tail -n +2 | grep -v '^$' | awk '{print $1}')
    fi

    if [[ -z "$devices" ]]; then
        err "No devices connected. Connect a phone and enable USB debugging."
        echo ""
        echo "Tip: Run '$0 --list' to see connected devices."
        exit 1
    fi

    # Process each device
    local exit_code=0
    for dev in $devices; do
        case "$mode" in
            pull)
                do_pull "$dev" "$dry_run" || exit_code=1
                ;;
            push)
                do_push "$dev" "$dry_run" || exit_code=1
                ;;
        esac
    done

    # Reminder about file_organizer
    if [[ "$mode" == "pull" && "$dry_run" != "true" ]]; then
        local file_count
        file_count=$(find "${LOCAL_STAGE}" -type f 2>/dev/null | wc -l | tr -d ' ')
        echo ""
        info "Files now in ${LOCAL_STAGE}: ${file_count}"
        info "The file_organizer daemon will pick these up on its next scan"
        info "and sync them to Proton Drive, Google Drive, etc."
        echo ""
        info "To run file_organizer immediately:"
        echo "  cd $(dirname "$(dirname "$0")") && ./organize test-real"
    fi

    return $exit_code
}

main "$@"
