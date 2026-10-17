"""Matching YouTube videos to catalog tracks by normalized title.

Only exact normalized titles match. There is no fuzzy matching: a wrong match is
worse than a miss, and misses stay visible as unmatched videos.
"""

import re
import unicodedata
from typing import Optional

# "(feat. X)", "[ft. X]", "(featuring X)".
BRACKET_FEAT_RE = re.compile(r"[(\[]\s*(?:feat|ft|featuring)\b\.?[^)\]]*[)\]]")
# Unbracketed "Song ft. X" / "Song feat. X" up to the end of the title.
TRAILING_FEAT_RE = re.compile(r"\s(?:feat|ft|featuring)\b\.?\s.*$")
# Video-only decorations: "(Official Video)", "[Lyrics]", "(Remastered 2020)", ...
VIDEO_TAG_RE = re.compile(
    r"[(\[]\s*(?:official\s+)?(?:music\s+|lyric\s+)?"
    r"(?:video|audio|visuali[sz]er|lyrics?|remaster(?:ed)?(?:\s+\d{4})?)\s*[)\]]")
# "- Remastered", "- Remastered 2011", "- 2011 Remaster".
REMASTER_RE = re.compile(r"\s[-–—]\s*(?:\d{4}\s+)?remaster(?:ed)?(?:\s+\d{4})?\s*$")
NON_WORD_RE = re.compile(r"[\W_]+")


def _fold(text: str) -> str:
    # NFKC folds full-width/compatibility forms; casefold handles Cyrillic too.
    return unicodedata.normalize("NFKC", text).casefold()


def normalize(title: str, artist: Optional[str] = None) -> str:
    """Comparable form of a track or video title.

    `artist` is given for official-channel videos, whose titles usually read
    "<Artist> - <Track>"; Topic-channel videos are titled by the track alone.
    """
    s = _fold(title)
    s = s.split("|", 1)[0]
    if artist:
        prefix = _fold(artist).strip()
        m = re.match(re.escape(prefix) + r"\s*[-–—]\s*", s.lstrip())
        if m:
            s = s.lstrip()[m.end():]
    s = BRACKET_FEAT_RE.sub(" ", s)
    s = TRAILING_FEAT_RE.sub("", s)
    s = VIDEO_TAG_RE.sub(" ", s)
    s = REMASTER_RE.sub("", s)
    return NON_WORD_RE.sub(" ", s).strip()


def match_videos(titles: list, videos: list, artist: str):
    """Assign videos to catalog rows.

    `titles` are in catalog order; `videos` need `.title` and `.official`.
    Returns (per_row, unmatched): per_row[i] is the list of videos for row i.
    A track can collect several videos (Art Tracks from a single and an album,
    a music video, ...). When several rows share a normalized title, the videos
    go to the first one so totals are never double-counted.
    """
    index: dict = {}
    for i, title in enumerate(titles):
        key = normalize(title)
        if key:
            index.setdefault(key, i)
    per_row = [[] for _ in titles]
    unmatched = []
    for v in videos:
        i = index.get(normalize(v.title, artist if v.official else None))
        if i is None:
            unmatched.append(v)
        else:
            per_row[i].append(v)
    return per_row, unmatched
