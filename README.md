# File Organizer

Copies, syncs, organizes, and optionally deduplicates your files — across
your computer, cloud drives, and Android phone.

> **⚠️ WARNING — USE AT YOUR OWN RISK**
>
> This tool copies, synchronizes, and can DELETE files across your system.
> - Always keep current backups.
> - Start in **test mode** and review the logs before real runs.
> - Review your config carefully — folder sync and deduplication can
>   delete real files when misconfigured.
> - No warranty is provided — see LICENSE.

---

## What it does

| Capability | What happens |
|------------|--------------|
| **Organize** | Scans folders and builds a tree of **soft links** under the `organized` folder — inside the `file_organizer` folder by default, anywhere you like via `output_base` — grouped by type, year, and content categories. Original files stay where they are. |
| **Folder sync** | Bidirectionally or one-way synchronizes folder pairs (e.g. computer ↔ Proton Drive, computer → external disk) with `rsync`. |
| **Phone sync** | Copies new files from your Android phone to a staging folder, from where they reach the cloud and your computer. Has a 10-minute grace period so photos you delete immediately are never backed up. |
| **Deduplication** | Optionally finds and removes duplicate real files by content hash. |
| **Auto Git** | Optionally runs `git init` in development folders that don't have one yet. |

The computer program runs on **macOS, Linux, and Windows** (rsync required for
folder sync). The phone program runs on **Android via Termux**, or on macOS.

---

## One program: `organize`

The **only program you run is `./organize`** at the top of this folder
(item 12). It detects whether it is on a phone or a computer and does the
right thing; everything else lives in `src/` so you are never tempted to
run it directly. Run it with no arguments and it tells you what it does and
which commands to use.

```bash
./organize              # prints what it does + the commands for THIS device
./organize test         # dry-run scan (safe) — both platforms
./organize test-real    # computer: one production scan
./organize scan         # phone: one production scan
./organize start        # run in the background   ./organize stop
./organize log          # follow the log   ./organize status
./organize setup        # first-time setup (config + will-it-work report)
./organize pull         # computer: pull phone staging over USB (adb)
```

The old names `src/manage_organizer.sh` and `src/manage_phone.sh` still work
— they are now thin forwards to `organize`.

---

## One config file for everything

Everything is configured from a single template: **`config_template.yaml`**,
which has two sections:

```
config_template.yaml
├── computer:   settings for the computer program
└── phone:      settings for the phone program (phone_sync)
```

- On the **computer**, `config.yaml` is created automatically from the
  `computer:` section the first time you run the program.
- On the **phone**, `phone_sync_config.yaml` is created automatically from
  the `phone:` section the first time you run `phone_sync`.
- The template uses placeholder paths like `/Users/janedoe`. If you run the
  program with an unedited config, it **stops and tells you exactly what to
  edit** — it will not silently do nothing.

### Editing a config file

| Platform | Open it with |
|----------|--------------|
| macOS | **TextEdit** (plain text mode) |
| Windows | **Notepad** |
| Android / Termux | **nano** (`nano phone_sync_config.yaml`) |

Two rules:

1. **Never use TAB characters** in YAML — only spaces. (The programs catch
   tabs and tell you plainly.)
2. Replace every placeholder path (`janedoe`, `YourExternalDrive`) with your
   real paths.

---

## Quick start — macOS or Linux

```bash
cd /path/to/file_organizer

# 1. First run: this creates config.yaml from the template and prints a
#    "Will it work?" report telling you which paths exist and which
#    placeholders you must edit.
./organize test

# 2. Open config.yaml with TextEdit (macOS) or your favourite editor,
#    replace the placeholder paths, then run the test again.
./organize test

# 3. When the test looks right, run for real (one cycle):
./organize test-real

# 4. Or run continuously in the background:
./organize start
```

Watch what it does:

```bash
./organize log        # tail the log (~/.file_organizer.log)
./organize status     # is it running?
./organize stop       # stop the background process
```

## Quick start — Windows

1. Install **Python 3.9+**, and **rsync** (e.g. via WSL, Git for Windows, or
   Cygwin).
2. Open a terminal in the `file_organizer` folder and run:

   ```
   py organize --scan-once
   ```

   (or `py -m file_organizer --scan-once` from inside `src\`)

3. This creates `config.yaml` and prints the "Will it work?" report. Open
   `config.yaml` with **Notepad**, replace the placeholder paths, and run the
   command again.
4. Symlinks in the `organized` folder require Administrator rights or
   Windows Developer Mode.

## Quick start — Android (Termux)

1. Install **Termux** from F-Droid (not the Play Store version).
2. In Termux:

   ```bash
   termux-setup-storage
   pkg install python rsync
   ```

3. Get the files onto the phone (pick one):

   ```bash
   # From a computer over USB — the whole program is 3 things:
   # organize, src/, and config_template.yaml
   adb push organize config_template.yaml /sdcard/Download/
   adb push src /sdcard/Download/file_organizer_src/
   # then, in Termux:
   mkdir -p ~/organize_home/src
   cp ~/storage/shared/Download/organize ~/organize_home/
   cp ~/storage/shared/Download/config_template.yaml ~/organize_home/
   cp -r ~/storage/shared/Download/file_organizer_src/file_organizer ~/organize_home/src/

   # Or directly on the phone:
   pkg install git
   git clone https://github.com/pdxrod/file_organizer.git
   ```

4. First run — creates `phone_sync_config.yaml` from the template's
   `phone:` section and prints a "Will it work?" report:

   ```bash
   cd ~/organize_home
   ./organize make-config
   ```

5. Edit the config with **nano** (spaces, never tabs):

   ```bash
   nano phone_sync_config.yaml
   ```

6. Test without copying anything:

   ```bash
   ./organize test
   ```

7. Run continuously:

   ```bash
   ./organize start
   ```

> On Samsung phones, a Termux session that stays in the foreground survives
> best. See the note in the Android section below.

---

## How the phone ↔ computer flow works

```
Phone (Termux)                         Computer                        Cloud
┌───────────────────────┐   ┌───────────────────────────┐   ┌──────────────────┐
│ phone_sync            │   │ file_organizer            │   │ Proton Drive /   │
│                       │   │                           │   │ Google Drive     │
│ DCIM/Camera ──────────┼──►│ ~/misc (via phone_pull.sh)│──►│ My Files/misc    │
│ DCIM/Screenshots      │   │         │                 │   │                  │
│ Pictures ─────────────┤   │         ▼                 │   │                  │
│ Documents ────────────┤   │ organized (soft links)    │   │                  │
│ Download ─────────────┤   └───────────────────────────┘   └──────────────────┘
│                       │
│ ◄─ 10-minute grace ──►│  files younger than min_age_minutes are never copied,
└───────────────────────┘  so photos you delete right away are never backed up
```

1. `phone_sync` scans the folders you configured, skips junk, waits out the
   grace period, and copies new files into `/storage/emulated/0/file_organizer_staging`
   (keeping their folder structure, e.g. `DCIM/Camera/IMG_001.jpg`).
2. On the computer, `./organize pull` fetches the staging folder over USB
   (or wireless ADB) into `local_stage`.
3. The computer's sync pairs then mirror that folder to Proton Drive, Google
   Drive, or an external disk.
4. `./organize push` copies files from the cloud back to the phone.

Deleting a photo on the phone during the grace period means it is never
copied. Deleting it later removes it from staging on the next cleanup.

---

## Everyday commands — computer

```bash
./organize {command}
```

| Command | What it does |
|---------|--------------|
| `start` | Start in the background (production mode, continuous) |
| `stop` / `restart` / `status` | Control the background process |
| `log` | Tail `~/.file_organizer.log` |
| `test` | Single **dry-run** cycle — nothing real is changed |
| `test-real` (alias `scan`) | Single production cycle |
| `sync` | Folder synchronization only |
| `dedupe` | Deduplication only |
| `clean` | Wipe the `organized` folder and the log, then a fresh scan |
| `cleanup` | Remove broken/stale soft links from the `organized` folder |
| `git-preview` / `git-init` | Preview or run auto-git |
| `collapse-renames` | Plan the 'foo (1).png' family collapse (dry-run; add `--REAL` to apply) |
| `gui` / `web` | Desktop GUI / web UI |
| `pull` / `push` / `list` | Phone ↔ computer over USB (adb) |
| `create-test` | Create a sample test environment |

## Everyday commands — phone

```bash
./organize {command}
```

| Command | What it does |
|---------|--------------|
| `start` / `stop` / `restart` / `status` | Control phone sync |
| `log` | Tail the log |
| `stats` | Show the sync database statistics |
| `test` | Single **dry-run** scan — no files copied |
| `scan` | Single production scan |
| `find-proton` | Locate the Proton Drive folder on the device |
| `make-config` | Create phone_sync_config.yaml from the template |
| `setup` | First-time setup wizard |

## Phone ↔ computer over USB (`organize pull`)

```bash
./organize pull                    # phone → computer (all phones)
./organize push                    # cloud → phone
./organize list                    # connected devices and stats
./organize pull --device ID        # one specific phone
./organize pull --dry-run          # show what would transfer
```

(These forward to `src/phone_pull.sh`; you can still call that directly.)
All paths come from the `phone_pull:` section of `config.yaml`. On macOS,
install ADB with `brew install android-platform-tools` and enable USB
Debugging in the phone's Developer Options.

---

## Test mode

`./organize create-test` builds a small environment with sample files under
`src/test/`. `./organize test` then runs a full dry-run cycle against it, so
you can see exactly what the program would do before you let it near your
real files. Nothing outside the test folders is touched.

The phone equivalent is `./organize test` on the phone (dry-run scan of the
real folders — it copies nothing).

---

## What gets organized / synced — and what never does

The `organized` folder contains only **soft links** — your original files
stay put. It lives inside the `file_organizer` folder by default; set
`output_base` in `config.yaml` to put it anywhere else (item 15).
Links are grouped by file type, year, and meaningful content categories
(short common words like "ready" or "wrote" are filtered out; distinctive
words like "yugoslavia" become categories).

A shared never-copy list lives in the config (both sections). Highlights:

| Kind | Examples | What happens |
|------|----------|--------------|
| Version control | `.git`, `.hg`, `.svn` | Never synced — back these up with `git push` |
| Environments | `.venv`, `venv`, `node_modules` | Never synced — recreate from lock files |
| Build output | `__pycache__`, `dist`, `build`, `.gradle` | Never synced |
| Junk files | `.DS_Store`, `*.pyc`, `*.log`, `*.crypt14`, `.Spotlight-V100` | Never synced |

These safety defaults are always in force — your config can only **add** to
them, never remove them.

## Configuration reference

Everything is documented with comments inside `config_template.yaml`. The
highlights:

**`computer:` section**

| Key | Purpose |
|-----|---------|
| `drives` | Shortcuts like `MAIN_DRIVE`, `PROTON_DRIVE` used elsewhere |
| `sync_pairs` | Bidirectional folder pairs (newest file wins) |
| `one_way_pairs` | One-way pairs (source → target only) |
| `source_folders` / `organize_folders` | Folders scanned for dedup / the `organized` tree |
| `output_base` | Where the `organized` soft-link folder lives (default: inside this folder) |
| `exclude_patterns` | Never-copy list (always merged with the built-in defaults) |
| `scan_interval` | How often the background process runs (seconds) |
| `idle_backoff_factor` / `idle_backoff_max_multiplier` | Sleep longer when cycles change nothing |
| `enable_folder_sync`, `enable_duplicate_detection`, `auto_git` | Feature switches (safe defaults off where dangerous) |
| `phone_pull` | Where phone files land and where push mode reads from |

**`phone:` section**

| Key | Purpose |
|-----|---------|
| `source_directories` | Folders on the phone to monitor |
| `target_directory` | Staging folder (`/storage/emulated/0/file_organizer_staging`) |
| `min_age_minutes` | Grace period before a new file is copied |
| `scan_interval_seconds` | How often to check for new files |
| `include_extensions` / `exclude_extensions` | Extension filters |
| `exclude_patterns` / `exclude_folder_names` | Never-copy lists (merged with defaults) |
| `proton_drive_paths` | Where `--find-proton` looks |

## Safety checklist before real runs

- [ ] You have current backups of anything important.
- [ ] You ran a dry-run (`test`) and reviewed the output.
- [ ] `config.yaml` has your real paths — no `janedoe` placeholders — and no TABs.
- [ ] Dedup is off, or you understand it deletes **real files** under `source_folders`.
- [ ] You know how to watch the log and stop the process.

If anything is unclear, stay in test mode.

## Platform support

| Platform | Status |
|----------|--------|
| macOS | Full support (primary platform) |
| Linux | Full support |
| Windows | Supported (symlinks need Developer Mode; rsync via WSL) |
| Android | Supported via Termux (phone sync + staging) |
| iOS | Not planned |

---

License: MIT — see LICENSE file.
