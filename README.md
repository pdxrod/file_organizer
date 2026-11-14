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
> - No warranty is provided.

---

## What it does

| Capability | What happens |
|------------|--------------|
| **Organize** | Scans folders and builds a tree of **soft links** under the `organized` folder — inside the `file_organizer` folder by default, anywhere you like via `output_base` — grouped by type, year, and content categories. Original files stay where they are. |
| **Folder sync** | Bidirectionally or one-way synchronizes folder pairs (e.g. computer ↔ Proton Drive, computer → external disk) with `rsync`. |
| **Phone sync** | Copies new files from your Android phone to a staging folder, from where they reach the cloud and your computer. New files are copied immediately by default (`min_age_minutes: 0`); raise it to 5-10 if you want a grace period so photos you delete right away are never backed up. |
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
                        # (if the device's config is missing or invalid, it
                        # opens the guided config editor instead — see 'config' below)

# Every platform
./organize auto         # production scan — just works
./organize test         # dry-run scan (safe) — changes nothing
./organize start        # run in the background
./organize stop         # stop the background program
./organize restart      # stop, wait, start again
./organize status       # is it running?
./organize version      # print the version number
./organize log          # follow the log file (Ctrl+C to stop)
./organize setup        # first-time setup (config + will-it-work report)

# Computer
./organize config       # change config.yaml section by section — a guided
                        # editor, no text editor or YAML knowledge needed.
                        # Enter keeps a value, e edits (a drive's name AND
                        # folder), x quits without saving, s saves and quits
                        # (on a phone the same editor edits phone_sync_config.yaml)
./organize test-real    # one production scan (alias: scan)
./organize sync         # sync folders only
./organize dedupe       # remove duplicate files only
./organize cleanup      # remove broken/stale symlinks
./organize clean        # wipe the organized folder + log, fresh scan
./organize git-preview  # which folders would get 'git init'
./organize git-init     # run auto-git for real
./organize collapse-renames  # plan 'foo (1).png' family collapse
./organize route        # PLAN where files go (by name + content)
./organize route-real   # actually move files into place
./organize route-status # show the plan / decisions so far
./organize route-undo   # put the last moved files back
./organize create-test  # build a small test environment
./organize gui          # desktop app
./organize web          # web UI
./organize pull         # pull phone staging over USB (adb)
./organize push         # push to the phone's staging over USB
./organize list         # list connected phones

# Phone
./organize scan         # one production scan (alias: scan-once)
./organize stats        # sync database statistics
./organize find-cloud-drives  # locate your cloud drive folders
./organize make-config  # create phone_sync_config.yaml
./organize rename       # rename held files, one by one (d deletes, x stops)
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
- The template uses placeholder paths like `/Users/janedoe` and
  `/Volumes/YourExternalDrive`. If you run the program with an unedited
  config, it **reports every placeholder or missing path loudly and keeps
  running** — those paths are skipped, so nothing is copied from them. It
  will not silently do nothing: the warnings tell you exactly what to
  edit.

### Editing a config file

On the computer, you don't have to edit `config.yaml` by hand — the
guided editor is the easy way:

```bash
./organize config
```

It walks you through every setting in plain words — drives, folders to
scan, sync pairs, one-way backups, the organized folder, feature toggles,
routing, phone pull, excluded names. At any prompt:

- **Enter** keeps the current value
- **e** edits it (for a drive: its name AND its folder)
- **x** quits without saving anything
- **s** saves everything entered so far and exits

The editor never writes a broken file: the result is validated first and
swapped in only if it loads, so your previous `config.yaml` survives any
failure. It writes spaces, never tabs, and placeholder paths like
`janedoe` or `YourExternalDrive` are fixed by editing the drive/folder
values there. The editor also opens automatically when you run plain
`./organize` with a missing or invalid `config.yaml`.

On the phone, the SAME editor edits `phone_sync_config.yaml` the same
way — source folders, the staging folder, cloud sync, the free-space
guard, the grace period, rename rules, scan timing, filters and
exclusions. It needs no PyYAML: it writes the flat YAML subset the
phone daemon's built-in parser reads, so it works on Termux as-is.
It also opens automatically when plain `./organize` finds the phone
config missing or invalid. `./organize make-config` still just creates
the file from the template with no questions.

Prefer a text editor? Both files are ordinary YAML, so if you know YAML
you can edit them directly: `config.yaml` with **TextEdit** (macOS),
**Notepad** (Windows) or any plain-text editor, and
`phone_sync_config.yaml` with **nano** on the phone. Two rules: use
SPACES for indentation, never tabs, and replace the placeholder paths
(`janedoe`, `YourExternalDrive`) with your real ones. The programs check
the result and tell you plainly when something is wrong.

---

## Quick start — macOS or Linux

```bash
cd /path/to/file_organizer

# 1. First run: this creates config.yaml from the template and prints a
#    "Will it work?" report telling you which paths exist and which
#    placeholders you must edit.
./organize test

# 2. Fix the placeholder paths with the guided editor, then test again.
./organize config
./organize test

# 3. When the test looks right, run for real (one cycle):
./organize test-real

# 4. Or run continuously in the background:
./organize start
```

Watch what it does:

```bash
./organize log        # tail the log (in the file_organizer folder: .file_organizer.log)
./organize status     # is it running?
./organize stop       # stop the background process
```

## Quick start — Windows

1. Install **Python 3.9+**, and **rsync** (e.g. via WSL, Git for Windows, or
   Cygwin).
2. Open a terminal in the `file_organizer` folder and run:

   ```
   py organize scan-once
   ```

   (or `py -m file_organizer scan-once` from inside `src\`)

3. This creates `config.yaml` and prints the "Will it work?" report. Fix
   the placeholder paths with the guided editor (`py organize config`),
   then run the command again.
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
   mkdir -p ~/file_organizer/src
   cp ~/storage/shared/Download/organize ~/file_organizer/
   cp ~/storage/shared/Download/config_template.yaml ~/file_organizer/
   cp -r ~/storage/shared/Download/file_organizer_src/file_organizer ~/file_organizer/src/

   # Or directly on the phone:
   pkg install git
   git clone https://github.com/janedoe/file_organizer.git
   ```

4. First run — creates `phone_sync_config.yaml` from the template's
   `phone:` section and prints a "Will it work?" report:

   ```bash
   cd ~/file_organizer
   ./organize make-config
   ```

5. Change the settings with the guided editor (no text editor needed):

   ```bash
   ./organize config
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
│ DCIM/Camera ──────────┼──►│ MAIN_DRIVE/Pictures…     │──►│ misc/…           │
│ DCIM/Screenshots      │   │ MAIN_DRIVE/Music, misc    │   │                  │
│ Pictures ─────────────┤   │         │                 │   │                  │
│ Documents ────────────┤   │         ▼                 │   │                  │
│ Download ─────────────┤   │ organized (soft links)    │   │                  │
└───────────────────────┘   └───────────────────────────┘   └──────────────────┘
│ ◄── grace period ───►│  files younger than min_age_minutes are never copied,
                         │  default 0 = immediate (set 5-10 to skip deleted photos)
```

1. `phone_sync` scans the folders you configured, skips junk, waits out the
   grace period, and copies new files into `/storage/emulated/0/file_organizer_staging`
   (keeping their folder structure, e.g. `DCIM/Camera/IMG_001.jpg`).
2. On the computer, `./organize pull` fetches the staging folder over USB
   (or wireless ADB) and maps each staging subfolder to a computer folder:
   a `staging_map:` override first, then a same-named folder under
   MAIN_DRIVE (e.g. `staging/Music` → `MAIN_DRIVE/Music`), then
   `MAIN_DRIVE/misc` (created on demand) for anything it can't place.
   With `phone_pull.auto_pull: true` (the default), every production
   `./organize auto` cycle does this pull automatically when a phone is
   connected — FIRST, before anything else, so the pulled files are
   organized and synced in that same cycle — no separate `pull` command
   needed.
3. The computer's sync pairs then mirror those folders to Proton Drive,
   Google Drive, or an external disk.
4. `./organize push` copies files from the cloud back to the phone.

Deleting a photo on the phone during the grace period means it is never
copied. Deleting it later removes it from staging on the next cleanup.

**Changing the grace period:** `min_age_minutes: 0` (the default) copies
files immediately. If you'd rather have a window to delete junk before it
gets backed up, run `./organize config` on the phone and set the
"Grace period (minutes)" to 5-10 — or edit `phone_sync_config.yaml`
directly. Restart the daemon afterwards (`./organize stop`, then
`./organize start`).

---

## Everyday commands — computer

```bash
./organize {command}
```

| Command | What it does |
|---------|--------------|
| `start` | Start in the background (production mode, continuous) |
| `stop` / `restart` / `status` | Control the background process |
| `log` | Tail `.file_organizer.log` (in the file_organizer folder) |
| `test` | Single **dry-run** cycle — nothing real is changed |
| `test-real` (aliases `scan`, `auto`) | Single production cycle |
| `sync` | Folder synchronization only |
| `dedupe` | Deduplication only |
| `clean` | Wipe the `organized` folder and the log, then a fresh scan |
| `cleanup` | Remove broken/stale soft links from the `organized` folder |
| `git-preview` / `git-init` | Preview or run auto-git |
| `collapse-renames` | Plan the 'foo (1).png' family collapse (dry-run; add `real` to apply) |
| `route` / `route-real` | Plan / apply smart routing into buckets by name + content |
| `route-status` / `route-undo` | Show routing decisions / put the last moves back |
| `gui` / `web` | Desktop GUI / web UI |
| `pull` / `push` / `list` | Phone ↔ computer over USB (adb) |
| `create-test` | Create a sample test environment |
| `config` | Guided editor: change config.yaml section by section, no text editor. Enter keeps a value, `e` edits (a drive's name AND folder), `x` quits without saving, `s` saves and quits. Runs automatically when you run plain `./organize` with a missing or invalid config.yaml |
| `setup` | First-time setup (config + will-it-work report) |
| `version` | Print the version number |

The desktop GUI (`./organize gui`) needs Tk 8.6+ — Apple's bundled
python3 has Tk 8.5, which draws an empty black window on modern macOS.
`organize gui` automatically launches with a newer python3 (e.g.
Homebrew's python-tk@3.x) when one is installed; if none is found it
tells you how to install it:

```bash
brew install python-tk@3.14
```

### Web UI (`./organize web`)

A small web page served on your computer, made for opening from a phone
or tablet on the same Wi-Fi network. It shows the `organized` folder's
categories and links, and has a Run Scan button to trigger one
production cycle. It runs on port 5000:

```bash
./organize web
```

The server prints the addresses to open — use the `this computer` one
in a browser on the computer, the `phone/tablet` one from other devices
on the same network. (Addresses like `utun` or VPN tunnels are not
reachable from a browser — opening one just hangs.)

`organize web` needs flask and launches with a python3 that has it,
installing guidance included:

```bash
brew install python@3.14 && python3.14 -m pip install flask
```

If port 5000 is taken (macOS AirPlay Receiver often claims it), the
server prints how to free it or pick another port.

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
| `scan` (alias `auto`) | Single production scan |
| `find-cloud-drives` | Locate your cloud drive folders (Proton Drive, Google Drive, Dropbox, …) |
| `config` | Guided editor for phone_sync_config.yaml — same conversation as on the computer (Enter keeps, `e` edits, `x` quits without saving, `s` saves and quits) |
| `make-config` | Create phone_sync_config.yaml from the template (no questions) |
| `rename` | Rename held app-generated files one by one (`name: X new name (d for delete, x for stop): ` — Enter keeps, `d` deletes, `x` stops the walk; a date in the name is prefilled as `YYYYMMDD-`) |
| `setup` | First-time setup wizard |
| `version` | Print the version number |

## Phone ↔ computer over USB (`organize pull`)

```bash
./organize pull                    # phone → computer (all phones)
./organize push                    # cloud → phone
./organize list                    # connected devices and stats
./organize pull device ID          # one specific phone
./organize pull dry-run            # show what would transfer
```

`./organize auto` already pulls automatically when a phone is connected
(`phone_pull.auto_pull: true` — the default); the commands above are for
manual or one-off use.

(These forward to `src/phone_pull.sh`; you can still call that directly.)
All paths come from the `phone_pull:` section of `config.yaml`. On macOS,
install ADB with `brew install android-platform-tools` and enable USB
Debugging in the phone's Developer Options.

### If the phone shows `unauthorized` (USB debugging)

`unauthorized` means USB debugging is OFF on the phone, or the phone's
"Allow USB debugging?" prompt was not accepted. `./organize auto` and
`./organize pull` detect this and print these steps:

1. Phone: **Settings → About phone → tap "Build number" 7 times**
   (unlocks Developer options).
2. Phone: **Settings → Developer options → turn ON "USB debugging"**.
3. Reconnect the USB cable and accept the **"Allow USB debugging?"**
   prompt on the phone (tick **"Always allow from this computer"**).
4. Run `./organize list` — the phone must show `device`, not
   `unauthorized` or `offline`.

Phones with USB debugging off warn about it themselves: `organize auto`
on the phone checks the setting and prints the same steps.

---

## Test mode

`./organize test` runs a full **dry-run** cycle against the folders in your
`config.yaml` — it scans, plans the link tree and the syncs, and changes
nothing, so you can see exactly what the program would do before you let it
near your real files. `./organize create-test` additionally builds a small
sample environment with files under `src/test/` (sample documents, pictures,
a git/venv project) that you can point a config at to try the program out.

The phone equivalent is `./organize test` on the phone (dry-run scan of the
real folders — it copies nothing).

A scripted walkthrough of the whole phone → computer → cloud pipeline is
below: **End-to-end test plan**.

---

## End-to-end test plan — phone → computer → Proton Drive

A repeatable walkthrough that starts from a clean slate and exercises the
whole pipeline: phone scan → staging folder → USB pull → organize (soft
links) → sync to Proton Drive. Steps marked ⚠️ are the ones you do by hand;
everything else is a script.

### 0. Preparation (computer, in the file_organizer folder)

```bash
./organize stop                          # no daemon running with the old config
python3 src/test/restore-major-folders.py   # if a previous run left files in /tmp, put them back first
python3 src/test/backup-major-folders.py    # sweep ~/Pictures ~/Downloads ~/Documents ~/Music ~/misc to /tmp
mv config.yaml /tmp/config.yaml.saved       # you will generate a fresh one
rm -rf organized                            # start with an empty organized/ folder
```

⚠️ From here until the one-way pairs from step 1 are in place, do NOT run
`./organize auto` (and don't `./organize start`): a both-ways pair would
refill the emptied folders with everything on Proton Drive.

### 1. Fresh computer config

Run `./organize config` (it opens automatically — config.yaml is missing).
Answer like this:

- **drives**: your real paths — MAIN_DRIVE = your home, PROTON_DRIVE = your
  Proton Drive folder, and so on. EXTERNAL_DRIVE may be left as a
  placeholder (missing drives are reported and skipped).
- **Folder sync: ON — with ONE-WAY pairs for this test.** The scripted
  checks assume three push pairs exist: `MAIN_DRIVE/Documents →
  PROTON_DRIVE/Documents`, `MAIN_DRIVE/Pictures → PROTON_DRIVE/Pictures`,
  `MAIN_DRIVE/misc → PROTON_DRIVE/misc`. Do NOT use both-ways pairs for
  these, and remove any pair whose SOURCE is on Proton Drive (e.g. Proton
  Music → MAIN Music). Reason: both-ways pairs copy FROM Proton Drive too,
  and with your folders emptied the next run would refill them with
  everything in the cloud. Nothing on Proton Drive needs emptying —
  pushes never delete anything there.
- **Duplicate detection: OFF**. Semantic categories: your choice.
- **Router: ON** (with Downloads and Desktop as watch folders) — the
  Downloads part of this plan relies on it moving files out of ~/Downloads.
- **Phone pull: auto_pull ON.** Note: the computer only pulls staged files
  older than `phone_pull.min_age_minutes` (5). Either wait 5+ minutes
  between the phone run and the computer run, or set it to 0 afterwards
  with `./organize config`… if it asks — otherwise edit one line:
  `nano config.yaml` → under `phone_pull:` → `min_age_minutes: 0`.

Sanity check: `./organize test` should plan without errors and without
"janedoe" placeholders.

### 2. Test files on the computer

```bash
# A tiny project in ~/misc — it must reach Proton Drive, its .git must NOT
mkdir -p ~/misc/sync_test_project/.git
echo 'print("alpha")'  > ~/misc/sync_test_project/a.py
echo 'print("beta")'   > ~/misc/sync_test_project/b.py
echo 'local git data'  > ~/misc/sync_test_project/.git/config

# One small file in each now-empty major folder
echo "pictures test"  > ~/Pictures/20261111_pictures_test.txt
echo "documents test" > ~/Documents/20261111_documents_test.txt
echo "music test"     > ~/Music/20261111_music_test.txt
echo "misc test"      > ~/misc/20261111_misc_test.txt
echo "downloads test" > ~/Downloads/20261111_downloads_test.txt
```

What to expect: Documents, Pictures and misc are PUSHED to Proton Drive
by the one-way pairs (nothing comes back down); the Downloads file is
MOVED out by the router into one of YOUR router's target folders
(whatever you configured); Music stays local (its one-way pair copies
FROM Proton Drive).

### 3. Test files on the phone

On the phone (Termux), in the file_organizer folder:

```bash
git pull
python src/test/create-files-on-phone.py
```

It creates six files: three ready to copy (`20261111_celtic.png`,
`20261111_phone_notes.txt`, `20261111_phone_download.txt`) and three held
app-generated ones (`Screenshot_20261114_666_X.jpg`,
`Screenshot_20261114_777_X.jpg`, `IMG_20261114_153021.jpg`).

⚠️ Make sure the phone's grace period is 0 (fresh files copy at once):
`./organize config` → "Grace period (minutes)" → `0` (or check
`phone_sync_config.yaml` says `min_age_minutes: 0`).

### 4. Rename on the phone

```bash
./organize rename
```

- For `Screenshot_20261114_666_X.jpg` type the new name
  `2026114_foo_baz.jpg` and press Enter (clear the date prefill if one
  appears — type the full name).
- For `Screenshot_20261114_777_X.jpg`: press **Enter alone** (keep it).
- For `IMG_20261114_153021.jpg`: press **Enter alone** (keep it).
- `x` stops the walk if you need to.

Keeping two of them held is the point: they prove held files are not
copied, while the renamed one now copies.

### 5. Phone production scan

```bash
./organize auto
```

Ready files are copied into `file_organizer_staging/` keeping their folder
structure; the held ones are logged as "Held until renamed".

⚠️ Check: `ls file_organizer_staging/DCIM/Screenshots` should show ONLY
`2026114_foo_baz.jpg` (no `777_X`, no `666_X`).

### 6. Computer production run

⚠️ Phone plugged in, **USB debugging ON**, and accept the "Allow USB
debugging?" prompt if it appears.

```bash
./organize list    # optional — shows the phone
./organize auto
```

This pulls the staging folder FIRST (into ~/misc and matching folders),
then organizes and syncs to Proton Drive in the same cycle. ⚠️ If it
pulled 0 files, the files are probably younger than
`phone_pull.min_age_minutes` — wait 5 minutes (or set it to 0, see step 1)
and run `./organize auto` again.

### 7. Verify

```bash
python3 src/test/verify-test-results.py
```

Prints PASS/FAIL per check: phone files pulled (and held ones not),
major-folder files in place, the router move, `~/misc/sync_test_project`
mirrored to Proton Drive with `.git` excluded, Proton mirrors of the phone
files, and organized/ soft links (intact, pointing at your real files).

⚠️ Then eyeball: open Proton Drive and check Documents/, Pictures/,
misc/ (sync_test_project with a.py + b.py and NO .git),
misc/DCIM/Camera/20261111_celtic.png; open organized/ and follow a few
links back to your files.

### 8. Tear-down (computer)

```bash
python3 src/test/restore-major-folders.py
```

Your real Pictures/Downloads/Documents/Music/misc contents come back
(test files stay — delete them if you like). Keep the fresh config or put
your old one back: `mv /tmp/config.yaml.saved config.yaml`.

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
| `dedupe_download_copies` | Auto-on: delete `foo_1`, `foo (1)`, `foo-1`, `foo (1) (1)` copies when an identical plain-named file exists, and rename trailing ` (N)` counters to `_N` before the extension (`foo.m4a (1)` → `foo_1.m4a`, different files kept) |
| `rsync_whole_file` / `rsync_io_timeout_seconds` | Whole-file transfers + stall guard for cloud drives (item 11) |
| `sync_lock_enabled` / `sync_lock_stale_minutes` | One-machine-at-a-time sync: the second machine skips a pair another device is syncing (stale locks are taken over) |
| `sync_chunk_subfolders` | Pairs with more subfolders than this are synced one subfolder at a time |
| `phone_pull` | Where phone files land and where push mode reads from |

**`phone:` section**

| Key | Purpose |
|-----|---------|
| `source_directories` | Folders on the phone to monitor |
| `target_directory` | Staging folder (`/storage/emulated/0/file_organizer_staging`) |
| `min_age_minutes` | Grace period before a new file is copied (minutes). Default 0 = copy immediately. Set 5-10 to give yourself time to delete unwanted photos before they're backed up. Change it with `./organize config` (the "Grace period" prompt) or edit `phone_sync_config.yaml` |
| `scan_interval_seconds` | How often to check for new files |
| `include_extensions` / `exclude_extensions` | Extension filters |
| `exclude_patterns` / `exclude_folder_names` | Never-copy lists (merged with defaults) |
| `cloud_drive_paths` | Where `find-cloud-drives` looks (any cloud app — Proton, Google, Dropbox, …) |
| `sync_cloud_drives` | Copy new files into every cloud drive found on the phone (into a `file_organizer` subfolder) — on by default |
| `min_free_space_mb` | Free-space guard — the daemon stops copying when the target has less than this |
| `hold_app_generated_names` | Hold `Screenshot_…`, `YYYYMMDD_HHMMSS.jpg` etc. until renamed — rename them with `./organize rename` (item 13) |
| `normalise_leading_dates` | `2026-09-23-party.jpg` → `20260923-party.jpg` on the copy (item 13) |

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

License: MIT.
