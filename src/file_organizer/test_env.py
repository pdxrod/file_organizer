"""Test environment: creates sample files and folders for testing."""

import os, random, shutil
from pathlib import Path

SAMPLE_TEXT_FILES = {
    "yugoslavia.html": """<html><body>
<h1>The Breakup of Yugoslavia</h1>
<p>Yugoslavia was a country in Southeast Europe during most of the 20th century.</p>
</body></html>""",
    "jeremy_corbyn_speech.txt": """Jeremy Corbyn addressed the crowd about peace in the Middle East.
The Labour leader spoke about inequality and justice for all people.""",
    "ella_fitzgerald_bio.md": """# Ella Fitzgerald
The First Lady of Song, Ella Fitzgerald was an American jazz singer.
She was known for her purity of tone and improvisational ability.""",
    "miles_davis_notes.txt": """Miles Davis - Kind of Blue
Recorded in 1959, this is the best-selling jazz album of all time.
Featuring John Coltrane and Cannonball Adderley.""",
    "budget_2026.csv": """Category,Amount,Notes
Rent,1200,Monthly
Food,400,Groceries
Transport,150,Gas and parking
Entertainment,200,Movies and dining""",
    "peggy_lee_song.txt": """Peggy Lee - Fever
Never know how much I love you
Never know how much I care
When you put your arms around me
I get a fever that's so hard to bear""",
    "random_notes.txt": """Some random thoughts about life and existence.
The meaning of something is often found in the search itself.
Ready to go when you are. Wrote this down for later.""",
}

SAMPLE_BINARY_FILES = {
    "screenshot_2026.png": b"\x89PNG\r\n\x1a\n" + os.urandom(2048),
    "profile_photo.jpg": b"\xff\xd8\xff\xe0" + os.urandom(1024),
}


def create_test_environment(config):
    """Create test directory with sample files."""
    test_dir = Path(__file__).parent.parent / "test"
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir()

    sources_dir = test_dir / "sources"
    sources_dir.mkdir()

    # Create sample text files
    docs = sources_dir / "documents"
    docs.mkdir()
    for name, content in SAMPLE_TEXT_FILES.items():
        (docs / name).write_text(content)

    # Create sample binaries
    images = sources_dir / "pictures"
    images.mkdir()
    for name, content in SAMPLE_BINARY_FILES.items():
        (images / name).write_bytes(content)

    # Create a project directory with .git and .venv
    project = sources_dir / "my_project"
    project.mkdir()
    (project / "main.py").write_text("print('hello world')\n")
    (project / "requirements.txt").write_text("requests==2.28.0\n")
    (project / ".git").mkdir()
    (project / ".venv").mkdir()

    # Create organized output
    organized = test_dir / "organized"
    organized.mkdir()

    print(f"Test environment created in {test_dir}")
    print(f"  Sources: {sources_dir}")
    print(f"  Organized: {organized}")
    return test_dir
