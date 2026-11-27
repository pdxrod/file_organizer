file_organizer (organize) — installed copy
==========================================

What you have
-------------
- `organize` — the whole program (organize files, sync cloud drives,
  deduplicate, auto-git). No Python needed; everything is bundled.
- `phone_pull.sh` — helper used for phone sync over USB.
- `config_template.yaml` — the settings template the guided editor starts from.
- `install.command` / `uninstall.command` — the installer you ran / the remover.
- The program's own files (config.yaml, the organized/ link folder and
  the log) are created HERE, next to organize, the first time you run it.

Getting started
---------------
1. Open a NEW Terminal (Mac) or PowerShell/CMD (Windows) window.
2. Type:  organize
   The first run walks you through the settings, section by section —
   you never have to edit config.yaml by hand.
3. Dry-run first:      organize test
4. Production run:     organize test-real     (one scan, then stops)
   or run in the background:  organize start  (repeats on a schedule;
   stop it with 'organize stop', watch it with 'organize log')
5. Desktop app:        organize gui          web UI: organize web

Useful commands
---------------
organize auto        one production scan — just works
organize sync        sync folder pairs only (cloud drives, external disks)
organize dedupe      remove duplicate files only
organize config      change settings section by section
organize status      is the background program running?
organize version     print the version number

Requirements for the extras
---------------------------
- rsync: needed for folder sync.
  macOS: install Homebrew, then   brew install rsync
         (the program finds it automatically).
  Windows: install rsync via MSYS2 (pacman -S rsync) or cwRsync, and make
  sure 'rsync' is on the PATH.
- adb (Android platform-tools): needed only for phone sync over USB
  (organize pull / push / list, and the automatic phone pull).
- Windows: 'organize push' and 'organize list' use a small bash script —
  install Git for Windows (Git Bash) for those two commands.

Uninstall
---------
Mac:     double-click uninstall.command in the install folder.
Windows: Programs → uninstall "file_organizer (organize)".
