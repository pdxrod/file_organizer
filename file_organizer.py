#!/usr/bin/env python3
"""Main entry point for File Organizer v2.

Usage:
    python file_organizer.py [OPTIONS]
    python -m file_organizer [OPTIONS]
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from file_organizer.cli import main

if __name__ == "__main__":
    main()
