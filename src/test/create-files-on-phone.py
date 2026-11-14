#!/usr/bin/env python3
"""Create the test files for the end-to-end plan ON THE PHONE.

Run in Termux, inside the file_organizer folder:

    python src/test/create-files-on-phone.py

Creates six small files under the phone's storage:

    DCIM/Camera/20261111_celtic.png            ready  -> gets copied
    DCIM/Screenshots/Screenshot_20261114_666_X.jpg   held  -> rename it later
    DCIM/Screenshots/Screenshot_20261114_777_X.jpg   held  -> keep it
    Pictures/IMG_20261114_153021.jpg           held  -> keep it
    Documents/20261111_phone_notes.txt         ready  -> gets copied
    Download/20261111_phone_download.txt       ready  -> gets copied

"--root PATH" overrides the storage root (useful to test this script
itself on a computer without touching a phone).
"""

import argparse
import sys
from pathlib import Path

# Small-but-valid image headers plus filler, and text a few KB long so the
# computer's min_file_size filter also sees them.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 2048
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 2048
TXT = (b"file_organizer phone test file\n" * 200)

FILES = [
    ("DCIM/Camera/20261111_celtic.png", PNG),
    ("DCIM/Screenshots/Screenshot_20261114_666_X.jpg", JPG),
    ("DCIM/Screenshots/Screenshot_20261114_777_X.jpg", JPG),
    ("Pictures/IMG_20261114_153021.jpg", JPG),
    ("Documents/20261111_phone_notes.txt", TXT),
    ("Download/20261111_phone_download.txt", TXT),
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", default="/storage/emulated/0",
        help="phone storage root (default: /storage/emulated/0)",
    )
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print(f"ERROR: {root} not found.")
        print("On a phone in Termux: run 'termux-setup-storage' once,")
        print("answer the permission prompt, then try again.")
        sys.exit(1)

    for rel, data in FILES:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        print(f"  created {path}")

    print()
    print("Created 6 test files:")
    print("  ready to copy:    20261111_celtic.png, 20261111_phone_notes.txt,")
    print("                    20261111_phone_download.txt")
    print("  held (not copied): Screenshot_20261114_666_X.jpg,")
    print("                    Screenshot_20261114_777_X.jpg,")
    print("                    IMG_20261114_153021.jpg")


if __name__ == "__main__":
    main()
