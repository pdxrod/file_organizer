"""Item 13: app-generated filenames are held until renamed; leading dates
are normalised to YYYYMMDD.

Two rules:

1. **Hold app-generated names** — files whose names were invented by an app
   (`Screenshot_20250831_190852_Gmail.jpg`, `20260409_050201.jpg`,
   `IMG-20240219-WA0000.jpg`, `PXL_20260101_123456789.jpg`,
   `BBl-Screenshot-1700019577590.jpeg`, `20260218_150629_LINE_B204.jpg`,
   `Screen Shot 2026-01-01 at 12.34.56.png`, …) are NOT copied anywhere
   until the user renames them to something meaningful. A user-annotated
   name (timestamp + real words, e.g.
   `20231203_014527_lehmann_shulevitz_hard_moral_choices.jpg`) is treated
   as renamed and IS copied.

2. **Normalise leading dates** — when a user-named file starts with a date
   in `YYYY-MM-DD`, `YYYY_MM_DD` or `YYYY.MM.DD` form (possibly after a
   short app prefix like `IMG-`), the date is squashed to `YYYYMMDD`:
   `2025-12-06-postal-annex-receipt.png` → `20251206-postal-annex-receipt.png`,
   `IMG-2026-01-01-birthday.jpg` → `IMG-20260101-birthday.jpg`.
   The rest of the name is untouched.

These are pure helpers; callers decide where they apply (phone staging,
phone_pull.sh, the computer's misc router).
"""

import re

# Family decorations from item 10: " (1)" groups and trailing "_1" may be
# appended to app-generated names before the extension, e.g.
# "20260703_100056 (1)_1.jpg" — the name is still app-generated.
_DECO = r"(?:\s*\(\d{1,3}\))*(?:_\d+)*"

# App-suffix words seen after a timestamp, e.g. 20260218_150629_LINE_B204.jpg
_APP_WORDS = (
    "line|whatsapp|signal|telegram|gmail|maps|chrome|facebook|instagram|"
    "messenger|files by google|snapchat|twitter|tiktok|reddit|b204"
)

# Patterns are matched case-insensitively from the start of the name.
# Patterns without an explicit $ keep matching when decorations follow.
APP_GENERATED_PATTERNS = [
    r"^screenshot[\s_-]",                        # Screenshot_20250831_190852_Gmail.jpg
    r"^screen ?(shot|recording)[\s_-]",          # "Screen Shot …", "Screen Recording …"
    r"^[a-z0-9]+-screenshot-\d+",                # BBl-Screenshot-1700019577590.jpeg
    r"^img-?\d{8}-?wa\d+",                       # IMG-20240219-WA0000.jpg
    r"^vid-?\d{8}-?wa\d+",                       # VID-20240219-WA0001.mp4
    r"^img[_-]\d{8}[_-]\d{6}",                   # IMG_20260101_123456.jpg
    r"^vid[_-]\d{8}[_-]\d{6}",                   # VID_20260101_123456.mp4
    r"^pxl_\d{8}_\d+",                           # Google Pixel
    r"^mvimg_\d{8}_\d+",                         # Google motion photo
    r"^panorama_\d{8}_\d+",
    r"^audio_\d{8}_\d+",                         # Samsung voice recorder
    r"^\d{8}_\d{6}" + _DECO + r"(?:\.[^.]+)?$",  # 20260409_050201.jpg (+ (1)/_1)
    r"^\d{8}_\d{6}[_-](?:" + _APP_WORDS + r")(?:[_-]|\.|$)",  # 20260218_150629_LINE_B204.jpg
    r"^whatsapp (?:image|video|audio|document|ptt|sticker) \d{4}-\d{2}-\d{2}",
    r"^photo_\d{4}-\d{2}-\d{2}[_-]\d{2}-\d{2}-\d{2}",   # Telegram photo_
    r"^snapchat-\d+",
    r"^dsc[nf]?[_-]?\d{3,5}(?:\.[^.]+)?$",        # DSC_0001.jpg / DSC01234.jpg
    r"^imag\d{4}(?:\.[^.]+)?$",                  # HTC IMAG0001.jpg
    r"^\d{4}-\d{2}-\d{2} \d{2}\.\d{2}\.\d{2}" + _DECO + r"(?:\.[^.]+)?$",
    r"^\d{4}-\d{2}-\d{2} at \d{2}\.\d{2}\.\d{2}",  # iOS "2026-01-01 at 12.34.56"
]

_COMPILED = [re.compile(p, re.IGNORECASE) for p in APP_GENERATED_PATTERNS]

# Leading date: optional short app prefix (IMG-, VID_, PXL-…), then a full
# date with -, _ or . separators.
_LEADING_DATE = re.compile(
    r"^(?P<pre>[A-Za-z]{0,4}[_-])?"
    r"(?P<y>(?:19|20)\d{2})[-_.](?P<m>\d{2})[-_.](?P<d>\d{2})"
    r"(?P<rest>.*)$"
)


def is_app_generated(name: str) -> bool:
    """True when `name` looks like it was invented by an app (hold it
    until the user renames it — item 13)."""
    if not isinstance(name, str) or not name:
        return False
    return any(p.match(name) for p in _COMPILED)


def normalise_leading_date(name: str) -> str:
    """Squash a leading YYYY-MM-DD / YYYY_MM_DD / YYYY.MM.DD date (possibly
    after a short app prefix like IMG-) to YYYYMMDD. Returns the name
    unchanged when there is nothing to normalise."""
    if not isinstance(name, str) or not name:
        return name
    m = _LEADING_DATE.match(name)
    if not m:
        return name
    month, day = int(m.group("m")), int(m.group("d"))
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return name  # not a real date
    return (
        (m.group("pre") or "")
        + m.group("y") + m.group("m") + m.group("d")
        + m.group("rest")
    )


# A date anywhere in a name: YYYY-MM-DD / YYYY_MM_DD / YYYY.MM.DD or
# plain YYYYMMDD, not glued to other digits on either side (so a phone
# timestamp like 1788321667280 or 12026090599 never counts as a date).
_DATE_ANYWHERE = re.compile(
    r"(?<!\d)"
    r"(?:(?P<y>(?:19|20)\d{2})[-_.](?P<m>\d{2})[-_.](?P<d>\d{2})"
    r"|(?P<y2>(?:19|20)\d{2})(?P<m2>\d{2})(?P<d2>\d{2}))"
    r"(?!\d)"
)


def date_prefix_in(name: str) -> str:
    """The 'YYYYMMDD-' prompt prefill for 'organize rename': the normalised
    date of the first valid YYYY-MM-DD / YYYY_MM_DD / YYYY.MM.DD or
    YYYYMMDD date found anywhere in `name` (month 1-12, day 1-31). '' when
    there is none — e.g. 'Screenshot-2026-09-05-999.txt' → '20260905-'.
    """
    if not isinstance(name, str) or not name:
        return ""
    for m in _DATE_ANYWHERE.finditer(name):
        y = m.group("y") or m.group("y2")
        month = m.group("m") or m.group("m2")
        day = m.group("d") or m.group("d2")
        if 1 <= int(month) <= 12 and 1 <= int(day) <= 31:
            return f"{y}{month}{day}-"
    return ""
