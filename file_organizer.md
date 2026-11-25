# This file is the early history of file_organizer, kept mainly just out of interest, to see how far the programme advanced from these rather primitive beginnings.

My files are all over the place. I want to make it easier to find them. If I create an image on my phone, I want it copied onto my Proton Drive, or Google Drive, and then automatically copied to my desktop or laptop - any device running file_organizer. 

file_organiser creates folders in ~/organized (spelt with a Z) containing soft links to real files, to make it easier for me to find them. It classifies files by their name, and by their contents. If a file's name contains 'yugoslavia', or its contents, including binary files, contains the word 'Yugoslavia', or it contains a picture of text containing the word 'Yugoslavia', it is supposed to put a soft link to it in ~/organized/yugoslavia.

For example, 
organized/yugoslavia contains
yugoslavia.html, a soft link to yugoslavia.html

But file_organiser finds any word of more than 4 characters and creates a folder
e.g. organized/ready contains    
2026-03-26-american-woman-in-gaza-dedicates-life-to-palestine.png
beach_body_ready_protein_world_protest.jpg
e-ticket-rome-cairo-bkk.png

The new file_organizer should only create folders with meaningful names, like 'jeremy-corbyn' or 'yugoslavia', not words like 'ready' or 'wrote.'

It should exclude program files. See config.yaml, sections
exclude_patterns:
empty_folder_patterns:
softlink_folder_patterns:

Synchronisation should work like this:
Under sync_pairs: in config.yaml, if it contains
      - "MAIN_DRIVE/misc"
      - "PROTON_DRIVE/misc"
it should find any files in MAIN_DRIVE/misc which are not on PROTON_DRIVE/misc, and copy them from MAIN_DRIVE/misc to PROTON_DRIVE/misc, and vice versa. If there is a file on both, which is later on one than on the other, its should copy the later file onto the older one.
Under one_way_pairs: in config.yaml
      - "PROTON_DRIVE/Music"
      - "MAIN_DRIVE/Music"
means 'only copy new or newer files from PROTON_DRIVE/Music to MAIN_DRIVE/Music, but not vice-versa.' 

Synchronising .git and similar folders is a pain. See if you can figure something out. Some of these folders can end up containing a lot more bits than the code. So backing up .git folders to the cloud takes forever and too much space. Currently, 
before backing up a folder containing a .git folder, or .venv, etc. - see softlink_folder_patterns: - the current file_organiser copies the .git or .venv etc. folder to ~/organised (spelt with an S), and replaces the original with a soft link. Thus
/Users/janedoe/dev/ml/Kun/.git 
becomes a soft link pointing to
/Users/janedoe/organized/Users/janedoe/dev/ml/Kun/.git
but git doesn't work well with soft links. See what you can do, and let me know what you're doing, please. 

Getting and Gitting
If 'auto_git' is true in config.yaml, for any folder underneath the auto_git_folders folders which do not have a .git folder in it, do a git init, with a new .gitignore file:  
  auto_git: true
  auto_git_folders:
    - "MAIN_DRIVE/dev"
This needs to be 'intelligent' - not every folder needs 'gitting'. ~/dev/ml/ does not need gitting. ~/dev/ml/epstein-files/ needs gitting, but ~/dev/ml/epstein-files/data/ doesn't.

Test mode - see ~/dev/file_organiser/README.md, and ~/dev/file_organiser/test/.

Like file_organiser, the new file_organizer should be runnable as a background daemon or a desktop app, runnable on Mac, Linux or Windows.

I would like you to create an Android Phone app version when the desktop version is finished, runnable in the background, so when I create a new file, e.g. a screenshot or a photograph, it will be automatically backed up to the cloud, e.g. either Google Drive or Proton Drive or both, depending on a config.yaml file. The file_organizer daemon running on my desktop and/or laptop would pick up the files from the cloud. And vice-versa - new files on the desktop or the laptop would be copied to my Android phone. This needs to be smart - all these devices are constantly creating new files, and most of them do not need to be copied around. 

Don't do an iPhone version for now - since I don't have an iPhone, I can't test it. 

If you run manage_organizer.sh without a config.yaml being present, it should generate a new simple config.yaml with almost everything turned off, using config_template.yaml, and explain to the user what to do. 
manage_organizer.sh test

What I need to do, is, 
- make it work just like the File Organizer on my Mac, but on the completely different file system of an Android. I hate the way Android apps list files in what look like folders, but are actually a combination of file extensions (e.g. .png .jpg etc. files go in 'Images') and file origins (e.g. Camera and Screenshots). Whatever the real file system is 'underneath' this, I want File Organizer to work on it.
- sync files according to a config.yaml file - I'll make a really simple one, with just the Proton Drive and the Android File System under 'sync_pairs:'. I just need to know how to refer to the Android File System in this config.yaml file. It should sync my photos, movie clips and screenshots with the Proton Drive. 
- produce an app which I can put on my phone. 

1. I've created some simple .txt and .png files, and put them in various folders on the phones, e.g. ons26.txt on one phone, ONA20.txt on the other. E.g. ons26.txt is in folder "Internal storage/DCIM/Screenshots" on my Android S26. ona20.txt is in /storage/emulated/0/DCIM/ on my Android A20. I've also created ona20.jpg on the Android A20 - it's in 'Images' - I can't see what folder this is. These files never get backed up to Proton Drive, whereas onm5.jpg, which I created on the Mac, gets backed up by the file_organizer running on the Mac. Proton Drive isn't doing what I want. 

2. The other problem is the opposite; it's too 'eager'. As soon as a photo, for example, gets created, it backs it up. But I delete most of my photos - like most people, I experiment. Now, I have to delete them in two places (or maybe three, if the file_organizer finds them and puts them on the Mac!) 

I may not be able to have a full-on file_organizer funning on my phones, but I'd like daemons which run continuously and occasionally copy new files - from Documents, Download, Pictures, DCIM and maybe some other places onto the 'misc' folder on Proton Drive. Is this possible?

Problem	Solution
Under-backup: .txt, .png, screenshots in DCIM, Documents, etc. never get backed up by Proton Drive	
phone_daemon.py monitors any folders you configure — not just camera photos. It finds all file types.
Over-eager backup: photos backed up immediately, then you delete them and have to clean up multiple places	min_age_minutes: 0 — files are copied immediately (the default). Set 5-10 if you want a grace window in which deleted photos are never backed up.

What DeepSeek created:
phone_daemon.py (755 lines) — The core daemon:

Runs on Android via Termux or macOS
Content-hash tracking (SHA-256 + SQLite) — rename/move a file, it won't re-copy
Grace period (min_age_minutes) gives you time to delete unwanted files
Preserves folder structure: DCIM/Camera/IMG_001.jpg → ProtonDrive/misc/DCIM/Camera/IMG_001.jpg
All the modes: --scan-once, --dry-run, --daemon, --status, --stop, --find-proton, --stats
Tested: files copy correctly, already-synced files are skipped, min_age filtering works, .tmp files excluded
phone_daemon_config.yaml (102 lines) — Heavily commented config template for your phones

manage_phone_daemon.sh (316 lines) — Start/stop/status/log/test management, same pattern as the existing manage_organizer.sh

phone_pull.sh (rewritten, 462 lines) — The old one-liner is now a full bidirectional ADB sync tool:

./phone_pull.sh pull — pulls from all connected phones to ~/PhoneMedia/<device_id>/
./phone_pull.sh push — pushes from ~/PhoneMedia/<device_id>/outbox/ to phones
./phone_pull.sh list — shows connected devices and their file counts
Supports multiple phones, dry-run, device-specific operations
Uses tar streaming for speed (no individual adb pull per file)
The architecture:
Phone (Termux)                    Mac                          Cloud
┌─────────────────┐     ┌──────────────────────┐     ┌─────────────────┐
│ phone_daemon.py │     │ file_organizer        │     │ Proton Drive    │
│                 │     │                       │     │                 │
│ DCIM/Camera ────┼──►  │ ~/PhoneMedia/ ───► sync_pairs ──►   misc/  │
│ DCIM/Screenshots│     │ (via phone_pull.sh)   │     │                 │
│ Pictures ───────┤     │                       │     │                 │
│ Documents ──────┤     │ ~/organized/          │     │                 │
│ Download ───────┤     │ (soft-link tree)      │     │                 │
│                 │     └──────────────────────┘     └─────────────────┘
│ ◄── 10 min ──►  │
│  grace period   │
└─────────────────┘

To get it on your phone:

 1. Install Termux from F-Droid on your A20 (test phone first)

 2. In Termux:
termux-setup-storage
pkg install python rsync
pip install pyyaml
 
 3. From Mac, push the files:
adb push file_organizer/phone_daemon.py /sdcard/
adb push file_organizer/phone_daemon_config.yaml /sdcard/
adb push file_organizer/manage_phone_daemon.sh /sdcard/
 
 4. In Termux, move and test:
cp /sdcard/phone_daemon.py ~/
cp /sdcard/phone_daemon_config.yaml ~/
python3 phone_daemon.py --find-proton    # locate Proton Drive folder
python3 phone_daemon.py --scan-once --dry-run  # test
./manage_phone_daemon.sh start          # go live

Next:

1. - make it more user-friendly - an app that does what we do on Termux?
2. - make filenames shorter and easier for users to understand - no 'daemon'
3. - stop copying these files and folders, either on the phone, or on the computer
  - "*.crypt14" 
  - "node_modules"
  - "_build"
  - "deps"
  - "ebin"
  - "dist"
  - "build"
  - "target"
  - ".next"
  - ".nuxt"
  - ".cache"
  - ".parcel-cache"
  - "coverage"
  - ".nyc_output"
  - "elm-stuff"
  - ".elixir_ls"
  - ".stack-work"
  - ".bundle"
  - "vendor"
  - "bundle"
  - "priv/static"
  - ".gradle"
  - ".m2"
  - "tmp/cache"
  - ".tmp*"
  - ".DS_Store"
  - "*.pyc"
  - "*.log"
  - ".Spotlight-V100"
  - ".TemporaryItems"
  - ".fseventsd"
  - ".DocumentRevisions-V100"
  - ".git", ".hg", ".svn", ".cvs",
  - ".venv", "venv", "env", ".env",
  - "node_modules", ".next", ".nuxt",
  - "__pycache__", ".pytest_cache", ".mypy_cache", ".tox",
  - ".cache", ".parcel-cache", "coverage", ".nyc_output",
  - "elm-stuff", ".elixir_ls", ".stack-work", "_build",
  - "deps", "ebin", "dist", "build", "target",
  - ".bundle", "vendor/bundle", "priv/static",
  - ".gradle", ".m2", "tmp/cache"
  
Incidentally, I got this list out of various files. I think everything like this should be in config files.

4. - Everything which is hard-coded in code files, which can be put in config files, put it there, e.g. PROTON_CANDIDATES

5. - Maybe, the phone config file and the config_template.yaml could be merged? Fewer files for the user to edit would be super. config_template.yaml could be divided into two sections: computer_config and phone_config. 

6. - Make the file_organizer find the files in misc, and move them to their correct folders. This will be quite difficult. Most of the files beginning with a YYYYMMDD or YYYY-MM-DD eg. 20260708 belong in ~/pol/2026, but not all of them. THEN create the folders in ~/organized containing soft links to files, the soft links named after categories, like yorkshire and driver. 

7. - Remove any reference to /Users/janedoe, /Users/janedoe/misc, or anything else specific to me. If you have to use a name, use 'janedoe'. When file_organizer runs without a config.yaml, or with a config.yaml copied from config_template.yaml, and it has /Users/janedoe in it, and /Users/janedoe does not exist, say so and exit - tell the user to edit the config file. 

8. - make any user action easier - for example, tell users to open files with TextEdit or Notepad, or nano on a phone. In the README, put all instructions as clearly as possible. 

9. - In both the phone app and the computer app, when they first start, if there is only a yaml config template file (config_template.yaml or phone_daemon_config.template.yaml), it creates a new config file, and tells the user if it will work, and how to edit it (for example, on the computer, if it has /Users/janedoe, and the user is not called Jane Doe, it explains how to edit config.yaml). Remind the user not to use tabs in a YAML file. It should be impossible to run the computer software without a config.yaml file, but the phone program can produce a new config file based on phone_daemon_config.template.yaml.

10. - don't rename files with _1 at the end. Copying foo.png over foo.png, just overwrite it if it's later and a different size. In both the phone and computer software, look out for (1) files, renamed by the Mac or a phone - e.g. 'foo (1).png', or 'foo (2).png', or 'foo (1) (1).png', or 'foo (1) (2).png' etc. or even 'foo (1) (1)_1.png'. Always find the latest, rename it foo.png, and delete all the files which end in (1), (2), etc.. 

11. The organizer backing up to PROTON_DRIVE is a bit hit-and-miss.

12. There could be just one program called 'organize' which runs either phone_daemon or file_organizer, depending on whether it's on a phone or a computer. Everything else it does is set by flags, e.g. --test-real. If you run ./organize, it tells you what it does, and what flags to use eg. test-real on the computer, or scan on the phone. 'organize' would be the only file in the top level file_organizer folder - all the other files should be in a subdirectory, so the user isn't tempted to run all the other programs. There are quite a few of them, and the average Joe might get confused. 

12. Currently, phone_daemon on Android puts all files from the folders it reads into /storage/emulated/0/file_organizer_staging. Change it so instead of just copying the files, it makes a copy of the file's original folder in /storage/emulated/0/file_organizer_staging. So files in /storage/emulated/0/Music would end up in /storage/emulated/0/file_organizer_staging/Music. A config file would contain the mapping from Android file structure to the computer, e.g. 
/storage/emulated/0/file_organizer_staging on the phone, and 
MAIN_DRIVE
LOCAL_STAGE
TEMP_STAGE
on the Mac. In other words, files from /storage/emulated/0/file_organizer_staging/Music/ end up in /Users/janedoe/Music/. 
It needs to be configurable so that any folder in /storage/emulated/0/file_organizer_staging/ which has an equivalent in MAIN_DRIVE, eg. /storage/emulated/0/file_organizer_staging/Music/ files end up in MAIN_DRIVE/Music/, and where the program can't figure out where a file goes, it puts it  in MAIN_DRIVE/misc/ - and it creates that folder if it doesn't exist. It will have default configuration, and it should be configurable, e.g. if Jane Doe wants /storage/emulated/0/file_organizer_staging/Music/ files to end up in /Users/janedoe/Entertainment/.

13. Don't back up files YYYYMMDD_NNNNNN.jpg etc. Screenshot... ...etc.  wait til I've changed their names. Find examples. Normalise all dates at beginning of filenames to YYYYMMDD. This includes files like IMG-YYYYMMDD... any file which has been created by an app, which I have not yet given a meaningful name to.

14. When the daemons are running, if an operation, e.g. bidirectional sync, dedup, auto_git results in no action, gradually increase the time the daemon sleeps between each operation.

15. The folder it creates, 'organized', full of soft links, should be located in the file_organizer folder by default, instead of in the user's home folder, but where it is should be configurable in a config file. 

16. There should not be a specific 'find-proton' flag. It might be 'find-cloud-drives' and it would find any: Google, Proton, or any other cloud drives. The config file should contain addresses of cloud drives on computers e.g. 
  PROTON_DRIVE: "/Users/janedoe/Library/CloudStorage/ProtonDrive-jdoe@protonmail.com-folder"
  GOOGLE_DRIVE: "/Users/janedoe/GoogleDrive"
On a phone, the daemon would look for cloud drives and try to synchronise them with drives on the phone - taking account of available disc space on the phone.

DeepSeek, 2026-10-04:
I've read file_organizer.md and surveyed the codebase. Here's my report.

Current state (relevant to ordering)
Repo is clean; last commits are all in. Baseline: python3 -m unittest discover -s test → 9 tests, OK.
Some items are already partially done, which changes their priority:
Item 12 (staging) — the phone daemon already preserves folder structure in file_organizer_staging (phone_daemon.py ~line 686). What's missing is the Mac side: phone_pull.sh still flattens everything into ~/misc (~lines 287, 354). So half of 12 is done; the other half (staging→computer folder mapping) is open.
Item 9 (first-run config) — partly exists: cli.py creates a starter config and exits when config.yaml is missing; the phone daemon already auto-generates its config from its template. What's missing is the quality of that experience: "will it work, and how do I edit it" messaging, the no-tabs warning, and the janedoe validation.
Item 7 (janedoe) — janedoe appears only in config_template.yaml. Cleanup is small; the real work is the startup validation that detects template paths that don't exist on the user's machine and exits with instructions.
Item 4 (hard-coded constants) — PROTON_CANDIDATES is still hard-coded in phone_daemon.py:50. A sweep is needed.
Item 3 (exclusion lists) — lists exist in config.py DEFAULTS and both YAML templates, but not your full list, and they're duplicated across files. Needs consolidation, not invention.
The 14 items, as I read them
Android app / user-friendly phone experience
Friendlier filenames — drop "daemon"
Consolidate all exclusion lists into config files
Move hard-coded constants (PROTON_CANDIDATES etc.) into config
Maybe merge phone config + config_template.yaml into one file
Route misc files to correct folders by date, then build meaningful-category soft links in ~/organized
Remove janedoe references; validate and exit with instructions when template paths don't exist
README + user guidance (TextEdit/Notepad/nano)
First-run config creation on both platforms with clear "will it work / how to edit" messaging; no tabs in YAML; computer must not run without config.yaml
No _1/(1) renames — overwrite if newer+different size; collapse foo (1).png families to the latest
Fix PROTON_DRIVE backup being hit-and-miss 12a. Single organize entry point; everything else in a subdirectory 12b. Staging→computer folder mapping (Music→MAIN_DRIVE/Music, fallback misc)
Don't back up app-generated names (Screenshot…, YYYYMMDD_NNNNNN.jpg) until renamed; normalise leading dates to YYYYMMDD
Idle backoff: increase daemon sleep when a cycle does nothing
Proposed order
Phase 1 — Config foundation: items 3, 4, 7, then 5 (S–M each) Config touches everything: 9, 12b, 13, and the app all read it. Getting the patterns and constants into their final shape first means we never edit the same YAML twice. 7 is a natural pair with 3/4 (validation needs to know what a "template default" path looks like). 5 (config merge) comes last in this phase so the merged file is written once, in final form — but this needs your decision ("maybe"), since it affects every script and doc. My recommendation: yes — one config_template.yaml with shared:, computer:, phone: sections, but keep the two live configs separate (phone's is per-device and git-ignored).

Phase 2 — First-run UX, docs, naming: items 9, 8, 2, 14 (S–M) 9 builds directly on Phase 1. 8 is the README rewrite. 2 (drop "daemon" from filenames/messages) must come before 12a so we restructure into final names once. 14 (idle backoff) is small and independent — a quick win to slot in here while we're in the daemon loops.

Phase 3 — Single entry point: item 12a (L) Top-level organize that detects Android/Termux vs desktop and dispatches; --test-real, --scan, etc.; no args prints full usage. Everything else moves to a subdirectory. Riskiest structural change, so it comes after config/UX settle and after the rename. This also resolves the stray phone_pull.sh.bak at top level. Needs your confirmation on layout (organize + src/) and whether manage_*.sh stay or fold into organize.

Phase 4 — Sync correctness: items 10, 12b, 13, 11 (M each, 11 unknown until diagnosed)

10 first: copy-overwrite rule + (1)-family collapse. This is destructive (deletes the (1)/(2) siblings), so I'll build it dry-run-first and it needs care around the phone's SHA-256 SQLite tracking so renames don't re-copy.
12b: make phone_pull.sh stop flattening; add the staging-subfolder→computer-folder mapping with misc fallback.
13: filename gate (hold app-generated names until renamed) + date normalisation module with your real examples.
11: this starts as diagnosis, not coding — run a verbose sync cycle, capture where PROTON_DRIVE copies go missing, then fix the actual cause (my suspects: rsync-vs-CloudStorage-mount timestamp/checksum quirks, partial-file handling, excluded hidden files). I won't promise a fix until we see logs.
Phase 5 — Smart organisation: item 6 (XL, deliverable in slices) The misc→correct-folder router keyed on date prefixes (with "not all date-prefixed files" exception heuristics), then the meaningful-category soft-link tree. Biggest and most domain-specific; do it only after Phase 4 so its inputs are stable. I'd deliver it as: dry-run router first, then real moves behind an approval gate, then link naming.

Phase 6 — Android app: item 1 (XL, last) Depends on 12a (entry point), 5 (config), 9 (first-run UX), 13 (don't back up junk), 2 (names). Decision needed: MVP as a Termux-friendly wrapper (termux-api notifications + widget, no APK, testable on your A20 immediately) vs a real APK (Kivy or Flutter, much bigger). My recommendation: Termux MVP first, evaluate an APK after.

Working method throughout
One item per commit; unittest tests added with every change (the suite is thin — 9 tests — so each phase also grows it).
Destructive operations (10, 6, 13's deletes) always get --dry-run output you can approve before --REAL.
manage_organizer.sh test as the pre-commit gate, plus --scan-once dry-runs on your real trees.
Questions before I start
Item 5: merge into one template (my recommendation), or keep two?
Item 12a layout: organize at top level + src/ subdirectory, OK? Keep manage_*.sh?
Item 1: Termux-based MVP app first, or go straight for an APK?
Item 11: want me to instrument and run a verbose sync now so we have diagnosis data before Phase 4?

DeepSeek, 2026-10-07 — progress so far:
Phase 1 done: exclusion lists consolidated into config (item 3); PROTON_CANDIDATES and softlink classification moved to config (item 4); fail-fast on unedited template paths (item 7); single merged config_template.yaml with computer:/phone: sections (item 5).
Phase 2 done: first-run will-it-work report + no-tabs errors (item 9); README rewritten with per-platform instructions (item 8); 'daemon' dropped from user-facing names — phone_sync.py, manage_phone.sh, PhoneSync (item 2); idle backoff so daemons sleep longer when a cycle does nothing (item 14).
Phase 3 done (item 12a): there is now ONE program — ./organize at the top level. It detects phone vs computer (flags --phone/--computer and ORGANIZE_PLATFORM override), prints full usage with no arguments, and every old command is now an organize subcommand: test, test-real, scan, auto, start, stop, restart, status, log, sync, dedupe, cleanup, clean, git-preview, git-init, create-test, gui, web, pull/push/list, setup, make-config, find-proton, stats. All other files moved to src/ (package src/file_organizer/, scripts src/phone_pull.sh, and thin manage_*.sh forwards kept for muscle memory). The stray phone_pull.sh.bak was deleted. Tests grew to 72 and the suite passes; end-to-end smoke tests on a scratch copy confirmed dry-run, production links, daemon start/status/stop, phone-mode dry-run, and ADB delegation.
Open questions from the plan still unanswered: item 1 MVP form (Termux wrapper vs APK — recommended: Termux first) and item 11 (verbose sync diagnosis — will be the first step of Phase 4d).

DeepSeek, 2026-10-07 — more progress:
Phase 4a done (item 10): no _1/(N) renames anywhere. New rename_family.py parses 'foo (1).png', 'foo (2).png', 'foo (1) (1).png', 'foo (1) (2).png', 'foo (1) (1)_1.png' families (plain _1 files and year suffixes like 'report (2026).txt' are deliberately NOT families); the newest member becomes the base name and the others are deleted, always with a dry-run plan first. The phone's copy step no longer invents _N names — a staged copy is overwritten when the incoming file is newer and a different size, otherwise the staged copy stays and the older variant is logged as not backed up. Both sides get a collapse_rename_families config flag (off by default, respects the phone's grace period); the computer also gets the 'organize collapse-renames' subcommand. 15 new tests (87 total).
Item 15 done: the 'organized' soft-link folder now defaults to organized/ INSIDE the file_organizer folder (named 'organized', since the program is 'organize'). output_base in config.yaml moves it anywhere; missing, 'organized' and the old '~/organized' values all mean the new default, so existing configs migrate automatically. organize clean, the web app, and the template all follow output_base. 5 new tests (92 total).
Phase 4b done (item 12b): phone_pull.sh no longer flattens pulls into ~/misc. Pulled files keep their file_organizer_staging subfolder structure and each subfolder is mapped to a computer folder: the longest phone_pull.staging_map prefix first (e.g. Music → MAIN_DRIVE/Entertainment), then a same-named folder under MAIN_DRIVE when it exists (staging/Music → MAIN_DRIVE/Music), then MAIN_DRIVE/misc (created on demand) with the staging path preserved underneath. Pulls also follow the item-10 overwrite rule (no _1 renames). Mapping rules live in config.py (map_staging_path, resolve_staging_map) and are mirrored in phone_pull.sh for the bash loop. 7 new tests (99 total).
Naming settled: the program is 'organize'; the soft-link folder is file_organizer/organized; ~/organized no longer exists or is used anywhere (remaining mentions are only the migration rules and docs). The live config.yaml was updated to output_base: "organized". The live organized/ folder is git-ignored.
Phase 4c done (item 13): new filename_rules.py grounded in real examples from the user's folders. App-generated names — Screenshot_…, Screen Shot/Recording …, YYYYMMDD_HHMMSS.jpg (with (1)/_N decorations), IMG/VID-YYYYMMDD-WA0001, IMG_YYYYMMDD_HHMMSS, PXL_/MVIMG_/Panorama_/Audio_, timestamp+app-name (20260218_150629_LINE_B204.jpg), WhatsApp Image …, Telegram photo_…, Snapchat-…, DSC/IMAG camera names, iOS timestamps — are HELD (not copied) until the user renames them; a timestamp with real words added counts as renamed and is copied. Leading dates in user names are normalised to YYYYMMDD on the staged copy (2026-09-23-party.jpg → 20260923-party.jpg, IMG-2026-01-01-birthday.jpg → IMG-20260101-birthday.jpg); the source keeps its name. Both rules are config flags (default true) on the phone (phone_sync) and in phone_pull.sh pulls, with dry-run previews. 15 new tests (107 total).
Phase 4d done (item 11) — diagnosed and fixed the PROTON_DRIVE hit-and-miss sync:
- The daemon log showed the cause: 'mmap: Operation timed out' and 'pread: Operation timed out' on Proton placeholder files, plus whole rsync runs timing out at 3600s (especially the huge dev pair, 642k files). The old rsync_disable_mmap flag added --no-whole-file, forcing the mmap-heavy delta algorithm to READ cloud placeholders — exactly what hangs.
- Fix 1: whole-file transfer is now the default (rsync_whole_file: true; the legacy rsync_disable_mmap key means whole-file too). The local source is streamed into a fresh cloud file without reading placeholder content.
- Fix 2: rsync_io_timeout_seconds (default 300) passes rsync --timeout so a stalled cloud read fails in minutes instead of hanging an hour.
- Fix 3: sync_chunk_subfolders now actually works — pairs with more than 30 direct subfolders sync one subfolder per rsync run, each with its own timeout, so one stuck folder can't kill the pair (this is what the 642k-file dev pair needed).
- Fix 4: Proton's own artifacts (.trashed-<timestamp>-<name> markers, .pending, .thumbnails) are now in the built-in safety excludes so rsync never tries to read them.
- Verified on the real mount: a dry-run of Proton pol → MAIN_DRIVE pol enumerated 8274 files and found 788 real differences (files missing on the computer that previous syncs never got to) — with chunking these now transfer piecemeal instead of timing out. 7 new tests (116 total).
- Note for the user: ~/Documents is protected by macOS privacy (TCC) — the daemon needs Full Disk Access for Documents/Desktop/Downloads pairs.
Cloud-drive generalisation: all Proton-specific finder wording is now generic. --find-proton → --find-cloud-drives (organize find-cloud-drives; the old names still work), the proton_drive_paths config key → cloud_drive_paths (old key still honoured), and the built-in candidates now cover Proton Drive, Google Drive, Dropbox, iCloud, OneDrive and Box on macOS/Windows/Android. Verified on this Mac: finds the mounted Proton folder. 4 new tests (119 total).
Per the user's spec the find-proton ALIASES ARE NOW REMOVED — there is no find-proton flag, subcommand, function, or legacy config key; only find-cloud-drives / cloud_drive_paths. The computer config's drives: section documents cloud-drive addresses (PROTON_DRIVE, GOOGLE_DRIVE, …). New phone-side cloud sync: with sync_cloud_drives: true the daemon finds cloud drive folders on the device and copies new files into each one under a file_organizer subfolder (tracked per-destination in the SQLite DB, which now has a target-aware uniqueness constraint with automatic migration), with a min_free_space_mb free-space guard that stops copying before the phone fills up. Verified end-to-end (copy to a found drive, no re-copy on the second run, held names skipped, --find-proton rejected). 5 new tests (124 total).
Item 10 duplicate cleanup, take 2 (the user's thousands of duplicates): 'organize collapse-renames' now also deletes plain '_N' duplicates (foo_1.png whose foo.png exists) — but ONLY when the contents are IDENTICAL (SHA-256); numbered files that are really different (web archives like combo_019.js) are kept. The (N)-family collapse gained a keep_differing safety mode used by the CLI/daemon: divergent families are left untouched. The phone's collapse cycle includes the _N pass too. 6 new tests.
Missing-drive behaviour (revised per the user's latest report): a drive which does not exist is REPORTED clearly and the program KEEPS RUNNING — '⚠️ Drive EXTERNAL_DRIVE ('/Volumes/YourExternalDrive') does not exist — will skip anything on it. Plug the drive in, or edit config.yaml and set your real paths.' Removable drives come and go, so a missing drive is skipped (scanner and sync engine already skip missing paths) and everything else still works. Missing FOLDERS (sync pairs, sources) still warn and are skipped. Same on the phone: placeholder paths warn instead of exiting.
phone_daemon_config.yaml is GONE (per the user's request: 'Only config_template.yaml is needed'). The file is deleted, its .gitignore entry removed, and every code path that honoured the legacy phone_daemon_config.yaml / phone_daemon_config.template.yaml names is removed: organize's phone_config_path() always uses phone_sync_config.yaml; phone_sync._find_template() looks only for config_template.yaml; _make_phone_config_from_template() always extracts the phone: section (flat configs without a section header still pass through); resolve_config_path() no longer falls back to the legacy name. The one legacy-name test was dropped.
New phone command 'organize rename' (the user's request after seeing 'Held until renamed (app-generated name): …/BBl-Screenshot-1788321667280.jpeg' in the scan output): walks the held files one by one and prompts 'name: X new name: ' — the user types a new name and presses Enter, and the file is renamed in place. Enter alone keeps the old name; a new name without an extension keeps the old extension; path separators and existing targets are refused (never overwrite); a name that still looks app-generated gets a warning because it will be held again. min_age is ignored so fresh screenshots can be renamed right away; the same exclude/extension/folder filters as a normal scan apply. Built on pure helpers in phone_sync.py (apply_rename, collect_held_files, rename_held_interactive with injectable input/print) wired as --rename-held. 13 new tests (142 total).
Rename, take 2 (the user's follow-up): the prompt is now 'name: X new name (d for delete): ' — typing 'd' or 'D' + Enter DELETES the file. And when the file name contains a YYYYMMDD or YYYY-MM-DD date, the prompt is prefilled with the normalised date so the user just fills in the rest: 'name: Screenshot-2026-09-05-999.txt new name (d for delete): 20260905-' → typing 'party' renames to 20260905-party.txt. The date is found anywhere in the name (not glued to other digits — phone timestamps don't count; month 1-12, day 1-31). On an interactive terminal the prefill is inserted as editable text via readline; otherwise (or when input is piped) it is printed after the prompt and prepended to what the user types ('d' still wins). 12 new tests (154 total).
Rename, take 3 (the user's follow-up: 'There might be a hundred files to rename, and currently you have to use ctrl-C to get out of the renaming loop'): the prompt now says 'name: X new name (d for delete, x for stop): ' and typing 'x' or 'X' + Enter stops the whole walk cleanly (the remaining held files are left exactly as they are; the walk reports how many are left). Ctrl-C/EOF still stop gracefully too. In the printed-prefill fallback 'x' wins over the prefill just like 'd' does, and when readline has put the date prefill on the line, an 'x' typed right after it ('20260905-x') is also recognised as a stop, so it can never become an accidental rename. Usage text (organize phone section), README, and the --rename-held argparse help now mention 'd deletes, x stops'. 4 new tests.
Placeholder warning, take 2 (the user's report: "there is no janedoe in my config.yaml" — organize start's placeholder advice hardcoded "replace 'janedoe' / 'YourExternalDrive'" even though the config only contained YourExternalDrive): _validate_path_placeholders now records the marker each hit actually matched (in the spelling used in the config) and the advice line names only those: "edit config.yaml and replace 'YourExternalDrive' with your own paths." report_first_run's setup step 2 got the same treatment — it lists the markers found in the config, with a plain fallback when none remain. 2 new tests.

If you approve this order, I'd start with Phase 1: items 3 and 4, then 7, then pause for your answer on the merge.

DeepSeek, 2026-11-01 — item 10 revised (the user's 'Unnamed Track' rule):
The user found '01 Unnamed Track 1.m4a (1)' files in ~/misc. Diagnosis: those names were created by ANDROID on the phone when the user copied one 'Unnamed Track' CD rip over a same-named DIFFERENT rip ('name.ext (1)' is Android's copy naming). phone_pull.sh pulled them into ~/misc, and the router then misclassified them as misc (pathlib's suffix of 'foo.m4a (1)' is '.m4a (1)', which matches no music extension) and left them 'already_there'.
The user's new rule (supersedes the old item-10 'overwrite if newer' behaviour):
  1. Never create or keep ' (N)' at the END of a filename; never damage the extension. A file named 'foo.m4a (1)' becomes 'foo_1.m4a' ('foo.m4a (2)' → 'foo_2.m4a') — the counter moves just BEFORE the extension and keeps its number; a taken _N name (a DIFFERENT file) falls forward to the next free number.
  2. Copying/moving onto a same-named DIFFERENT file never overwrites: the original is kept and the incoming file becomes name_1.ext, name_2.ext, …
  3. Identical content is still deduped (the copy is deleted); different content is always preserved.
Implemented in rename_family.py (parse_trailing_counter, strip_trailing_counter, normalize_copy_counter, next_free_name, plan_trailing_counter_fixes + tree helper), the router (suffix-aware extension checks, _N collision moves, in-place renames for already-placed '(N)' files, re-decide of old trailing-counter state entries, old 'collision' entries now retried as _N moves), phone_sync.copy_file (never overwrites a different file; normalises trailing counters at copy time), phone_pull.sh pull+push loops (same rule, with cmp for identical skips), and the ./organize auto dedupe step now repairs trailing counters across organize/source folders on every cycle.
Also fixed: 'organize auto' getting stuck at 'checking for a connected phone'. adb devices/adb shell can block FOREVER on a wedged adb server or on a phone waiting for the 'Allow USB debugging?' prompt. Every adb call in phone_pull.sh now runs through adb_t (hard timeout: 20 s quick commands, 600 s tar stream / file pushes), adb_devices_raw restarts the adb server once and retries when adb produces nothing, and a device with no usable source dirs is skipped with a hint to check the phone's screen. The organize cycle can no longer hang on the phone. Tests grew accordingly (249 → 263) and the full suite passes.
Follow-up (same day): the user noticed the dedupe pass deleting 'duplicate' files inside /Users/rod/misc/.tmp_pull. That folder is phone_pull.sh's own temp staging (phone_pull.temp_stage under local_stage): Phase 1 streams the phone into it and Phase 3 rm -rf's it when the pull finishes (leftovers mean an interrupted pull). The bug: the auto-cycle tidy-up tree walks descended into hidden folders, so it tidied the transient staging copies as if they were user files, and the router could have routed files out of it mid-pull. Fixed: all rename_family tree walks (download_copies_in_tree, trailing_counter_fixes_in_tree, collapse_in_tree) prune dot-directories, and the router's _excluded skips any file under a hidden folder beneath a sweep root. Verified against the real ~/misc: zero planned actions inside .tmp_pull and the router reports such files 'hidden'.

DeepSeek, 2026-11-05 — the 'organize config' guided editor (the user's request after being told to hand-edit YAML and 'remember not to use tabs'):
Telling users to open config.yaml in a text editor, and remember never to use tabs, is too much to ask. New subcommand 'organize config' — an intelligent, interactive editor that walks through config.yaml SECTION BY SECTION in plain words: drives (with per-path status: ✓ exists / ✗ starter example / ? not available, plus hints for MAIN/PROTON/GOOGLE/EXTERNAL), source folders, sync pairs (both-ways, shown as A ⇄ B), one-way backups (A → B), the organized folder location, and one keep/flip question per feature toggle (folder sync, content analysis, duplicate detection with its delete warning, semantic categories, auto-git, collapse-renames, download-copy tidy-up), followed by the router (target folders per category, route roots, watch folders, DeepSeek + API key path — only asked when the router is on) and the phone pull (auto_pull, local/remote stage, adb path — only when it is on), and excluded names. Pressing Enter always keeps the current value; Ctrl+C/EOF cancels with nothing written; a review screen precedes saving, and the result is dumped with yaml.safe_dump (spaces only, tabs are impossible), loaded back through Config for validation, and only then moved into place with os.replace — the previous config.yaml survives any failure, so the editor can never leave a broken file behind. An invalid or missing config (tabs, YAML syntax errors, empty files, circular drive references) is explained in plain language and rebuilt from the safe-off starter before the walk.
Auto-launch rule: running plain './organize' (no arguments) when config.yaml is ABSENT OR INVALID now opens the editor automatically instead of printing usage ('./organize help' still prints usage). organize start and organize setup do the same when the config is missing or broken, and cli.py now catches YAML syntax errors (which used to crash with a traceback) and points at './organize config'. On a phone, 'organize config' explains it is a computer command (phone settings live in phone_sync_config.yaml via make-config). Everything is unit-testable: every prompt goes through injectable input_fn/output_fn (config_editor.py), so the whole walk is driven by scripted answers in tests. 54 new tests (286 → 340), full suite passes. v2.5.0.

DeepSeek, 2026-11-06 — wrong-device commands explain themselves (the user's report: './organize test-real' on the phone said 'unrecognized argument'):
Every subcommand that exists on only ONE device is now computed from the two command tables (COMPUTER_ONLY_COMMANDS / PHONE_ONLY_COMMANDS — they can never drift out of sync), and map_command turns them into an explanation instead of letting them fall through to the local CLI's argparse error. On a phone: test-real, sync, dedupe, cleanup, clean, git-preview, git-init, collapse-renames, create-test, route/sort family, gui, web, pull/push/list, config all print "is a computer-only command — it exists only on a computer" plus the closest phone equivalent ('./organize scan' is the production scan; route points at './organize rename'; pull/push/list point at the computer). On a computer: rename, dry-run, stats, find-cloud-drives, make-config print the same the other way round (stats → './organize status'/'./organize log'; find-cloud-drives/make-config → './organize config'). scan-once stays a valid computer flag (deliberately NOT phone-only), --config FILE stays a passthrough on both devices, and commands that exist on both (test/scan/auto/start/stop/status/…) are untouched. The usage text notes that wrong-device commands explain themselves. 10 new tests (340 → 350), full suite passes. v2.5.1.

DeepSeek, 2026-11-06 — organize config editor, take 2 (the user's report: typed 'e' to edit a drive and the menu just reprinted; also wants exit keys):
- Exit keys everywhere: 'x' quits immediately without saving (nothing written, same convention as './organize rename'); 's' SAVES EVERYTHING ENTERED SO FAR and exits 0. Both work at every prompt — asks, y/n confirms, and all three menu editors. To make 's' lossless, the section editors now mutate the config in place (edit_drives/edit_path_list/edit_pairs no longer copy their inputs; the walk setdefaults the keys first), so a save-and-quit in the middle of a section keeps that section's partial edits. 's' before anything was loaded saves a safe starter (everything off).
- Drives menu rework (the 'e' confusion): bare 'e' now works — with one drive it edits it, with several it asks 'which number?'; 'e4' still edits drive 4 directly, and 'r'/'remove' mirror it. Editing a drive now changes BOTH the name AND the folder ('name [GOOGLE_DRIVE]:' then 'folder [...]'), with a guard that refuses a rename onto an existing name. The same bare-e/bare-r ergonomics apply to the folder-list and sync-pair menus. Prompts, the intro section text, the usage text, and the README all spell out: Enter keeps, e edits (name AND folder), x quits without saving, s saves and quits.
26 new tests (350 → 376), full suite passes. v2.5.2.

DeepSeek, 2026-11-20 — the hours-long syncs were openrsync mmap-reading Proton Drive placeholders, and the sync engine now skips work that doesn't need doing (the user's request: 'quickly determine that folder A has no files with a later datetime than folder B, and skip syncing'):
- Diagnosis (measured on the real pairs): macOS /usr/bin/rsync is openrsync, which silently IGNORES the long --whole-file spelling the engine was passing. Its default delta mode mmap-reads every file's content during the scan; on Proton Drive each mmap force-downloads the placeholder. Measured: a dry-run over the 8.9k-file Proton pol folder took 10+ minutes with the old args and ~1.3s with -W.
- Fix 1: the engine now passes -W (whole-file), which openrsync AND GNU rsync both honour, and passes --no-whole-file only for a real GNU rsync (openrsync already defaults to delta, which is exactly what whole_file: false asks for).
- Fix 2: the engine prefers a real GNU rsync when one is installed (config key rsync_binary overrides; otherwise /opt/homebrew/bin/rsync and the other Homebrew paths are probed once). 'brew install rsync' makes the syncs use it automatically.
- Fix 3 (quick check): before each rsync direction the engine walks both trees ONCE in Python (measured: 0.7s for 8.9k Proton files, 32-42s for the 360k-666k-file dev sides) and skips the direction when the target already has every source file with the same size, permission mode and mtime (symlinks by target string, dirs by presence — dir mtimes are NOT compared because Proton doesn't preserve them). The rsync exclude list is applied with matching semantics (basename globs, /name/ anchored dirs, a/b suffix matches); any pattern shape the matcher can't mirror exactly makes the quick check decline and rsync runs as before. One-way pairs skip whenever the source has nothing newer, even when the target has extra files (there is no --delete). sync_quick_check: false disables it. 25 new tests (25 in test_sync_quick_check.py); the full suite passes.
- Known gaps (deliberate): when something HAS changed, rsync still scans that direction as before (no --files-from candidate lists — left for later); empty-dir creation and dir-mtime convergence happen on the next full rsync run; the first sync of a big out-of-sync pair (dev: ~300k files only on the computer side) still moves real data — that part is bandwidth, not scanning.

DeepSeek, 2026-11-20 — failed cloud syncs are remembered, not retried (the user's report: 'if a rsync error occurs when attempting to sync with a cloud drive, don't keep trying' — the log showed the same stuck Proton file failing again and again: 'error: …/Bonzos…/01 Part 1….mp3: mmap: Operation timed out'):
- Why it still failed after -W: openrsync mmaps the SOURCE file to read it during a real transfer, and some Proton placeholders stall on mmap — whole-file (-W) fixed the scan storm but not the transfer of a stuck file. The durable cure is a real GNU rsync (streams with read(), no mmap); the engine auto-uses one once installed (rsync_binary).
- What changed: a failed sync involving a CLOUD folder (Proton Drive, Google Drive, Dropbox, OneDrive, iCloud, Box — NOT removable /Volumes/ drives) is now remembered in ~/.file_organizer_sync_failures.json (config: sync_failure_state_path) and skipped on later runs for sync_failure_cooldown_minutes (default 360 = 6 hours, 0 = always retry), then tried once again — so a stuck file fails ONCE instead of every cycle, other chunks keep syncing, and the retry after the cooldown heals itself when the placeholder materializes or GNU rsync arrives. A successful run clears the memory; entries older than four cooldowns are pruned; a corrupt state file is ignored. 8 new tests (test_sync_failure_cooldown.py); full suite passes.

DeepSeek, 2026-11-20 — 'organize test' got stuck restoring, and the restore now resolves itself (the user's report: restore printed 'some items could not be restored because a same-named item already exists at the target', and the next sweep refused, leaving the plan at a 'compare and merge them by hand' dead end):
- What happened on the real machine: the 11:08 sweep moved the pair folders into the temp backup; the daemon's old-code Music sync (dying on Proton mmap stalls) then re-created same-named folders in ~/Music — PARTIALLY — so restore's skip-on-collision left 734 items behind with the manifest pinned.
- The fix: restore-major-folders.py no longer skips and stalls. It always drains the backup: items missing at the target move back; items whose BYTES match the target are dropped as duplicates (content-hashed — size/mtime alone is never used to delete, after a new test caught two different files sharing a size+timestamp); real conflicts are moved back beside the originals renamed '<name>.file_organizer-backup' (numbered if taken) so nothing is overwritten and nothing blocks the next sweep. The manifest is removed once drained.
- Ran it on the stuck backup: 7,176 files put back into ~/Music, 739 duplicates dropped, 0 real conflicts (the apparent 'differences' were mtime-only duplicates). The plan is unblocked. 9 backup/restore tests (3 new), full suite passes.

DeepSeek, 2026-11-20 — 'organize test' was rebuilt around small throwaway folders, and the phone's grace period is gone (the user's run report: the test took 12.5 hours on a Mac Air M5 because it emptied and re-synced ~/Music from Proton Drive, failed 5 checks, told the phone user to change a 'grace period' setting, named its helper scripts, and mentioned other organize commands mid-run):
- Grace period removed: the phone no longer has a configurable min_age_minutes — PhoneSync.min_age is always 0 and new files copy immediately. The phone config editor's 'Grace period' question, the template key, the test's 'your grace period is N minutes' block, and the README's grace-period docs are gone (the computer's phone_pull.min_age_minutes stays — it's a different feature, and the test sets it to 0 in its own temporary config).
- New computer half: instead of sweeping real pairs (which emptied ~/Music and took 12.5h to refill), the test creates a few small files in throwaway 'file_organizer_test/' folders on MAIN_DRIVE and PROTON_DRIVE, builds a temporary config with ONLY those pairs (the real config.yaml is never touched), runs one full cycle against them, verifies, and deletes the folders. A test run is now minutes. It still proves: both-ways sync (each side's file reaches the other; a tiny project crosses without .git), one-way sync, phone pull (held files stay), router move out of ~/Downloads, and real Proton read/write. Missing external drives are reported, not failed.
- The Downloads router test file is now over the router's 1 KB minimum size (it was being skipped by min_file_size, one of the 5 failures).
- Output reduced and de-jargoned: no helper-script names, no 'sweep', and other organize commands are only mentioned at the very end (./organize auto for one cycle, ./organize start for the background). The verifier no longer needs the backup manifest and the old sweep scripts are no longer part of the flow (they remain for manual use). Full suite passes.

DeepSeek, 2026-11-21 — generic wording, unwrapped messages, and the old create-test scaffolding removed (the user's review: no Proton-specific phrasing, no mid-paragraph carriage returns, 'This app' / 'files which are ready' / 'For each file' / 'App-named files', and 'get rid of foo/ bar/ baz/ and the months-old code that copies files there and softlinks them in organized/'):
- The computer half no longer assumes Proton Drive: it picks the first EXISTING folder from the config's drives other than MAIN_DRIVE (Proton, Google, external — whatever exists) as the test's second side; with no such folder it runs the same test between two local folders. Messages say 'cloud folder' / 'second folder' instead of 'Proton Drive'. The verifier checks that the second side exists and was written (noting whether it is outside MAIN_DRIVE) instead of checking a PROTON_DRIVE key.
- Phone messages reworded: 'This app creates test files on the phone…', 'Next: the renames. For each file:', 'copies the files which are ready…', 'App-named files like 'Screenshot…' and 'IMG…''. Paragraphs are no longer split by hard carriage returns — each paragraph is one line and the terminal wraps it naturally.
- 'organize create-test', the --create-test flag, src/file_organizer/test_env.py and the src/test/foo|bar|baz sample files are deleted (git rm); README and usage text updated. Full suite passes.

DeepSeek, 2026-11-21 — the phone pull gives up fast when the phone stops answering (the user's run: 'checking for a connected phone' for 6+ minutes with the Python title flickering): 'adb devices' saw the phone but every 'adb shell' hung, so each call burned its full 20s timeout and the tar stream its 600s — minutes of stalls. do_pull now checks the adb_t exit code (124 = killed by the watchdog) after the getprop and each directory check and stops immediately with 'The phone is connected but is not answering — unplug and replug it…' instead of stalling. Also: 'press Enter alone' -> 'press Enter' everywhere, and the phone's closing prompt now leads with 'GO BACK TO THE COMPUTER.' in capitals so the handover is unmissable. Full suite passes.

DeepSeek, 2026-11-21 — the tar stream was the last unbounded stall (the user's second report: still six minutes at 'checking for a connected phone'): getprop and the dir checks now abort on a wedged phone, but the Phase-1 adb exec-out tar stream still waited its full 600s timeout. The stream's exit code is now checked too (124 = watchdog kill) and aborts with the same 'unplug and replug the phone' advice. Wording: 'pull files from a phone' / 'reroute files from your downloads folder', and the phone's closing banner is now 'TIME TO RETURN TO THE COMPUTER.'. Full suite passes.

DeepSeek, 2026-11-21 — the phone test no longer moves real photos, which was the last 'stuck pull' (the user's third report: six minutes at 'checking for a connected phone'): the trace showed the phone answering every shell command, then Phase 1 tar-streaming file_organizer_staging — which had been filling with HUNDREDS of real photos because each phone-side 'organize test' ran a full production scan. Now the phone test stages only its six test files: create-files-on-phone.py writes into a throwaway file_organizer_test/ source folder, run_phone_test runs PhoneSync against a temporary config (source = the test folder, target = a throwaway file_organizer_test_staging/, its own temp DB, cloud sync off), and the computer's test config pulls ONLY phone_source_dirs = file_organizer_test_staging. The phone's real photos are never touched, and the pull is seconds. Test-only files (temp config + db) are cleaned up. Also 'press Enter alone' is now just 'press Enter' in the rename walk's own messages. Full suite passes.

DeepSeek, 2026-11-21 — the pull deadlock, finally traced to its source (the user's fourth report): the Phase-2 loop used process substitution, `done < <(find ...)` — on macOS bash 3.2 the find child blocks on a full pipe while the read loop spins, with stale bash processes from earlier runs still holding the pipe's read end. The pull now writes the file list to a plain temp file and reads it back (same fix in push mode). The auto pull also streams its output live now — 'checking for a connected phone...' is followed by visible progress instead of a silent wait. Platform generality (the user's request: 'work out where the downloads folder is, and create it if it doesn't exist'): test_plan and the verifier find the downloads folder by NAME in the router's watch folders (fallback: the platform default, created when missing) and print its real path instead of '~/Downloads'; the phone half derives the phone's storage root from the real config's source folders instead of hard-coding /storage/emulated/0. Full suite passes.

DeepSeek, 2026-11-21 — the auto phone pull is now Python, ending the bash loop saga (the user's fifth report of the endless loop): src/phone_pull.sh kept deadlocking on macOS bash 3.2 (process substitution, command-substitution pipe-holds, watchdog subshells, and finally a spinning read loop with find blocked on a full pipe). New src/file_organizer/phone_pull.py replaces it for 'organize auto' and the end-to-end test: plain subprocess with hard timeouts (20s per command, 600s stream with watchdog kill), a streamed 'r|' tarfile processed member-by-member inside the loop, the same filename rules (held app-named files stay, dates normalise, counters move), staging-map placement, never-overwrite with content comparison, min-age and state tracking. Tested end-to-end with a fake adb that streams a real tar (ready files land in misc with correct paths, held files skipped, second run skips unchanged). The bash script stays only for manual 'organize push/list' use. Full suite passes.

DeepSeek, 2026-11-21 — the computer-side pull age gate is gone (the user: 'I'd rather any file on the phone gets dealt with — either it needs renaming cos it's a Screenshot… IMG… etc, or it gets copied'): phone_pull.min_age_minutes no longer exists. The Python puller copies every file straight away (held app-named files still wait for a rename, on the phone); the bash phone_pull.sh lost its age check, is_old_enough, MIN_AGE_MINUTES plumbing, help text and the config key (defaults + template). Only the unchanged-since-last-pull state check remains, which is dedup, not a gate. Full suite passes.

DeepSeek, 2026-11-22 — 'organize pull' now runs the Python puller (the user's report: pull copied two files then looked stuck for 10+ minutes while the menu-bar title flickered between 'Python' and 'Python organize test'): the bash phone_pull.sh loop spawns three Python processes per file, so the real phone staging (5,400 files) would take hours, and those per-file spawns were the flicker. 'organize pull' now uses the in-process Python puller (one adb stream, no per-file subprocesses, the real config's staging map); push/list still use the bash script. Also traced the recurring 'stuck' state to its source: killing a mid-stream pull orphans the phone-side 'tar cf -', which wedges the adb transport so every later 'adb shell' burns its full 20s timeout — 'adb kill-server' clears the wedge (the phone then asks to re-authorize once). Full suite passes.

DeepSeek, 2026-11-22 — intelligent file sorting (the user's spec: 'if it can figure out what's in the files, as well as their names, it should be able to put them in folders'): the router now sorts into eight buckets with year subfolders read from the NAME or the CONTENT — 20260914_vidia707_1.jpg → ~/Documents/2026 when its OCR text is a property listing (a photo OF a document is a document, not a picture), ilhan-omar-apology.png → ~/pol/2019 (the year comes from the OCR'd 2019), 'Gaza 2024_ TC and Minassian.pdf' → ~/pol/2024, 01-06-18-vienna-camping-….jpg → ~/Pictures/2018 (DD-MM-YY leading dates count), 20260815_bletchleypark2.mp4 → ~/Videos/2026. New 'videos' bucket (non-political videos leave ~/Pictures), OCR'd doc-signals for media (apartment/for sale/invoice/…), media reaches DeepSeek with scene labels even without text, a media extension never lets DeepSeek call a video a picture, and no year in name or content means the file stays flat in its target (mtime never guesses a year). Duplicates are now DELETED (the sorted copy survives, the incoming copy goes away), and stale applied decisions whose file is back at its origin are re-decided under the current rules — the superseded flat copy is removed when the file re-routes into a year subfolder. Every moved file gets organized/<keyword>/ soft links pointing at its FINAL home (organized/vienna/ → ~/Pictures/2018/…); route-undo removes them again, and the auto cycle's orphan cleanup now keeps links to router destinations (~/Videos and ~/dev are outside source_folders). Also fixed a latent entry-orphan bug: _entry() created state dicts without storing them, so phase-2/3 updates (the content year) were silently lost. config: router.target_folders.videos, year_subfolders, year_subfolder_categories (template + live config + editor). Full suite passes (475 tests).
DeepSeek, 2026-11-24 — router categories are now user-defined (the user: 'is it possible for a user to add whatever categories they want? it looks a bit rod-ish… by default it ships with political and ml_development, and I don't suppose many people are like that'): the keys of router.target_folders ARE the categories — add, remove or rename them, and the whole cascade works off that list. Built-in recognition (extension fast paths, keyword tiers, scene labels, DeepSeek descriptions) only runs for categories that actually have a home: a file that would land in an unconfigured bucket now goes to misc, and the political/ml/dev keyword tiers stay inert instead of producing 'no target folder' errors when those buckets aren't configured. User-defined categories get keyword hints from router.keywords.<name> (words or short phrases, matched in filenames, extracted text and OCR); the DeepSeek system prompt is built from the configured list, so the model only proposes real categories and an off-list answer is coerced to misc. The shipped template now has a generic set — documents, pictures, videos, music, development, misc — with political/ml_development shown as a commented example of adding your own, and './organize config' walks the user's existing categories and lets them add new ones with keyword hints. Nothing is ever hardcoded into a destination: a configured category with an empty path is disabled, and a decision with no target is still an explicit error, never a silent move (the no-hardcoded-targets test still passes). The user's own config.yaml (all eight buckets) keeps working exactly as before. Full suite passes (494 tests).

DeepSeek, 2026-11-24 — the shipped defaults are no longer rod-specific, and './organize config' now walks the WHOLE router section (the user: '~/dev and ~/misc are also rod-specific… how do users edit this router stuff? a normie would not be able to copy and paste from a comment in config.yaml; everything needs to be configurable'): the template's categories are now documents → ~/Documents, pictures → ~/Pictures, videos → ~/Videos, music → ~/Music, development → ~/Development, and the catch-all misc → ~/Downloads (Downloads exists on every computer, so files the router can't classify land where people already look — the route-roots inbox and the phone-pull local/remote stage also moved from MAIN_DRIVE/misc to MAIN_DRIVE/Downloads, and the dev examples in sync_pairs / auto_git_folders / tidy.exclude_folders are MAIN_DRIVE/Development). The router editor now covers everything: categories (keep/remove/add with folders), keyword hints for EVERY category (not just new ones — blank removes them, and legacy 'ml' hints migrate to 'ml_development'; the router accepts both keys), route roots, watch folders, watch-on-cycles, rename-by-content folders, year-sort folders, year subfolders + which categories get them, the DeepSeek block (API key path, model, media, confidence, batch size, calls per run, chars per file, timeout), the OCR block (on/off + budget) and the largest content-file size (asked in MB). Every value shows its current setting on the line, so Enter keeps everything. Full suite passes (499 tests).

DeepSeek, 2026-11-24 — the template is now platform-neutral and the editor suggests drive-style folders (the user: 'I don't think ~/ should appear in config_template.yaml… there should be comments containing a couple of Windows paths… everything should be as generic, platform-independent and easy to configure as possible'): the template's target_folders now default to MAIN_DRIVE/Documents, MAIN_DRIVE/Pictures, MAIN_DRIVE/Videos, MAIN_DRIVE/Music, MAIN_DRIVE/Development and the catch-all MAIN_DRIVE/Downloads, with a note that ~/Documents, C:/Users/you/Documents and /home/you/Documents all work too (expanduser handles ~, and a test pins that ~/Documents still lands in the home folder). The drives block now documents the three platform path styles (macOS /Users/janedoe, Windows C:/Users/janedoe with the Git-Bash /c/Users/janedoe form noted, Linux /home/janedoe), and adb_path gets a per-platform example comment. './organize config' now shows a suggested MAIN_DRIVE/... folder on the line for each standard category — Enter accepts it, typing over it uses the typed folder, clearing the line skips — so a normie can build the whole category table from key/value pairs without knowing path syntax. A category with an empty path is off (media falls to misc, no error), pinned by a new test. Full suite passes (501 tests).

DeepSeek, 2026-11-24 — the config editor now resolves drive shortcuts when it reports folder status (the user, mid-walk: '1. MAIN_DRIVE/pol (? not available right now…) — I don't know what that means. At this point, shouldn't organize know if these folders exist?'): path_status used to check the literal string 'MAIN_DRIVE/pol' on disk, so every drive-style entry showed the unhelpful '?'. It now resolves MAIN_DRIVE/… (and any other drive:) through the loaded drives map first and reports the REAL folder — '✓ exists', '✗ not available right now (will be skipped if missing)', or '✗ starter example' when the drive itself is still a template placeholder (/Users/janedoe). A ref whose drive name isn't defined gets an explicit '? drive X is not defined under drives: — add it first'. The drives map is threaded through edit_path_list and edit_pairs, so every list in the walk (source folders, sync pairs, one-way pairs, auto-git folders, route roots, watch folders, rename/year folders) shows the true status; the auto-git example also says MAIN_DRIVE/Development now. Full suite passes (504 tests).

DeepSeek, 2026-11-24 — wording (the user, walking './organize config': 'name [MAIN_DRIVE] (Enter keeps): MAIN_DRIVE' should be 'name [MAIN_DRIVE] (hit Enter to keep it): MAIN_DRIVE'): every keep-prompt in the computer editor now says '(hit Enter to keep it)' — the drive name and folder prompts, the list-entry edit prompt, the organized-tree folder, the DeepSeek API key path, and the prefill docstring example. Full suite passes (504 tests).

DeepSeek, 2026-11-24 — no '~/' remains anywhere in config_template.yaml, values or comments (the user: 'there are still many examples of ~/… I think they should all be changed to MAIN_DRIVE'): the output_base and legacy-organized comments now say MAIN_DRIVE/organized / MAIN_DRIVE/organised, sync_failure_state_path is now MAIN_DRIVE/.file_organizer_sync_failures.json (resolved at config load like log_path/pid_path, so drive refs AND ~/ both work), and the phone section's cloud_drive_paths now ships EMPTY — the phone daemon's built-in candidate list (macOS, Windows, Android) is used, with commented /Users/janedoe examples — instead of shipping a ~/-based list. A user typing ~/ into './organize config' or config.yaml is still accepted everywhere (expanduser at load + tests). Also fixed the keyword-hints prompt (the user, at 'hints for political [none]:', typed 'corbyn palestine gaza' and asked whether that's right, and whether multi-word hints like 'Donald Trump' work): commas separate hints and a comma item with spaces stays one phrase ('corbyn, donald trump'), while plain space-separated words become one hint each ('corbyn palestine gaza' → corbyn, palestine, gaza) — the prompt teaches this with an example and prints the parsed hints back for instant confirmation. Phrase hints on built-in categories match case-insensitively as phrases (pinned by a router test). Full suite passes (507 tests).

DeepSeek, 2026-11-24 — hints prompt wording (the user): the walk now says 'Enter a series of hints, separated by commas, e.g. 'donald trump, iran'', and prints the parsed list back ('hints for political: donald trump, iran'). Full suite passes (507 tests).

DeepSeek, 2026-11-24 — credential files are never copied anywhere (the user, on the rogue-AI scare: 'if a file looks like an API key, and it may not have such an obvious name as deepseek-api-key.txt, it should never be copied anywhere by organize'): new credential_guard.py with (a) NAME patterns — *.pem/*.ppk/*.pfx/*.p12/*.key/*.keystore/*.jks/*.kdbx, id_rsa*, .ssh, .env*, .netrc/.npmrc/.pypirc/.htpasswd/.git-credentials, *credential*, *password*, *secret*, token names, *api*key*, kubeconfig, service-account*.json — merged into the built-in exclude_patterns so sync, the organized link farm and the router all skip them whatever the user's list says, and (b) CONTENT sniffing for small (<=64 KiB) text-ish files: sk-…, ghp_/github_pat_, AKIA/ASIA…, AIza…, xox…, Stripe/Twilio keys, Telegram bot tokens, PEM/PRIVATE KEY blocks, JWTs, and key=value assignments with a real-looking value (placeholders like 'your-key-here' don't count). Enforcement: sync_engine walks LOCAL sides before each rsync and adds per-file --exclude patterns (cloud sides are never read — placeholders materialise — so they get name protection only); phone_pull skips credential files mid-pull; the phone daemon's copy_file refuses them; the router leaves them put ('credential file' skip reason). Trade-off, deliberately: '*.key' also blocks Apple Keynote decks (rename to .keynote). Tests pin the name rules, key formats, placeholder handling, rsync excludes, cloud-side non-sniffing and router skip. Full suite passes (521 tests).

DeepSeek, 2026-11-25 — WhatsApp pictures are no longer invisible to the phone's scan (the user, on their S26: downloaded WhatsApp pictures named IMG-… and './organize rename' couldn't find them): the template's phone section shipped 'whatsapp' in exclude_folder_patterns, so the scan skipped every folder whose name contains whatsapp — including Pictures/WhatsApp, which the SAME template lists as a source directory, so the download-then-rename workflow the owner wanted never fired (IMG-… names ARE held candidates; the scan just never reached them). The template and the phone config editor's starter now omit whatsapp from exclude_folder_patterns, with a comment explaining it is deliberate and how to re-exclude. Existing phones keep their generated phone_sync_config.yaml, so the fix there is './organize config' → remove 'whatsapp' from Excluded folder patterns (and make sure the real save folder is a source directory). Full suite passes (523 tests).

DeepSeek, 2026-11-25 — only ONE organizer may run at a time (the user, via 'organize gui': hit Start, then Stop said 'no file organizer found' while the log kept moving; ps showed two organizers and an rsync — a pre-existing daemon plus the GUI's): there was no single-instance check anywhere — the daemon ('python -m file_organizer --REAL') never registered a PID, and the GUI's Stop used pgrep -f 'python.*file_organizer', which fails on macOS where the interpreter's argv[0] is 'Python' (capital P) — so Stop and Exit killed nothing. New single_instance.py: the daemon takes an exclusive flock on the pid file (macOS/Linux) and writes its PID; a dead holder's file is taken over automatically. Every start path now refuses a second instance: the CLI daemon itself (the authoritative guard), './organize start' (pre-check before spawning), the GUI's Start button ('already running (PID n)'), and one-shot production runs ('stop the daemon first'). Stopping goes through the same pid file: the GUI's Stop kills the recorded PID (SIGTERM, 5s grace, then SIGKILL), then falls back to a scan for 'file_organizer' excluding itself; SIGTERM is handled in the daemon so the pid file is removed on stop. Verified live: a second daemon and a one-shot REAL both refuse with the message, and SIGTERM removes the pid file. Full suite passes (530 tests).
