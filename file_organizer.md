# This file is the history of file_organizer, formerly file_organiser

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
/Users/janedoe/organised/Users/janedoe/dev/ml/Kun/.git
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

I may not be able to have a full-on file_organizer funning on my phones, but I'd like daemons which run continuously and occasionally copy new files - from Documents, Download, Pictures, DCIM and maybe some other places onto the 'My Files/misc' folder on Proton Drive. Is this possible?

Problem	Solution
Under-backup: .txt, .png, screenshots in DCIM, Documents, etc. never get backed up by Proton Drive	
phone_daemon.py monitors any folders you configure — not just camera photos. It finds all file types.
Over-eager backup: photos backed up immediately, then you delete them and have to clean up multiple places	min_age_minutes: 10 — files must survive a 10-minute grace period before being copied. Delete unwanted photos in that window and they're never backed up.

What DeepSeek created:
phone_daemon.py (755 lines) — The core daemon:

Runs on Android via Termux or macOS
Content-hash tracking (SHA-256 + SQLite) — rename/move a file, it won't re-copy
Grace period (min_age_minutes) gives you time to delete unwanted files
Preserves folder structure: DCIM/Camera/IMG_001.jpg → ProtonDrive/My Files/misc/DCIM/Camera/IMG_001.jpg
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
│ DCIM/Camera ────┼──►  │ ~/PhoneMedia/ ───► sync_pairs ──►  My Files/ │
│ DCIM/Screenshots│     │ (via phone_pull.sh)   │     │     misc/       │
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

1 - make it more user-friendly - an app that does what we do on Termux?
2 - make filenames shorter and easier for users to understand - no 'daemon'
3 - stop copying these files and folders, either on the phone, or on the computer
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

4 - Everything which is hard-coded in code files, which can be put in config files, put it there, e.g. PROTON_CANDIDATES

5 - Maybe, the phone config file and the config_template.yaml could be merged? Fewer files for the user to edit would be super. config_template.yaml could be divided into two sections: computer_config and phone_config. 

6 - Make the file_organizer find the files in misc, and move them to their correct folders. This will be quite difficult. Most of the files beginning with a YYYYMMDD or YYYY-MM-DD eg. 20260708 belong in ~/pol/2026, but not all of them. THEN create the folders in ~/organized containing soft links to files, the soft links named after categories, like yorkshire and driver. 

7 - Remove any reference to /Users/janedoe, /Users/janedoe/misc, or anything else specific to me. If you have to use a name, use 'janedoe'. When file_organizer runs without a config.yaml, or with a config.yaml copied from config_template.yaml, and it has /Users/janedoe in it, and /Users/janedoe does not exist, say so and exit - tell the user to edit the config file. 

8 - make any user action easier - for example, tell users to open files with TextEdit or Notepad, or nano on a phone. In the README, put all instructions as clearly as possible. 

9 - In both the phone app and the computer app, when they first start, if there is only a yaml config template file (config_template.yaml or phone_daemon_config.template.yaml), it creates a new config file, and tells the user if it will work, and how to edit it (for example, on the computer, if it has /Users/janedoe, and the user is not called Jane Doe, it explains how to edit config.yaml). Remind the user not to use tabs in a YAML file. It should be impossible to run the computer software without a config.yaml file, but the phone program can produce a new config file based on phone_daemon_config.template.yaml.

10 - don't rename files with _1 at the end. Copying foo.png over foo.png, just overwrite it if it's later and a different size. In both the phone and computer software, look out for (1) files, renamed by the Mac or a phone - e.g. 'foo (1).png', or 'foo (2).png', or 'foo (1) (1).png', or 'foo (1) (2).png' etc. or even 'foo (1) (1)_1.png'. Always find the latest, rename it foo.png, and delete all the files which end in (1), (2), etc.. 

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
If you approve this order, I'd start with Phase 1: items 3 and 4, then 7, then pause for your answer on the merge.
