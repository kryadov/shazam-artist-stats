#!/usr/bin/env python3
"""Offline tests for matching — title normalization and video assignment."""

import unittest
from collections import namedtuple

from matching import match_videos, normalize

V = namedtuple("V", "id title official")
ARTIST = "PERSONAL ANIMOSITY"


class Normalize(unittest.TestCase):
    CASES = [
        # (catalog or video title, artist prefix to strip, expected)
        ("ENOUGH", None, "enough"),
        ("bloody tears (for Julia) [feat. BLAZER 993]", None, "bloody tears for julia"),
        ("PAIN2 (feat. elox1m)", None, "pain2"),
        ("PAIN2 ft. elox1m", None, "pain2"),
        ("ASD (featuring renfry)", None, "asd"),
        # Cyrillic: casefold, punctuation and '#' collapse.
        ("СТРАХ", None, "страх"),
        ("#вялоелицо", None, "вялоелицо"),
        ("Эй, братан!", None, "эй братан"),
        # NFKC folds full-width characters.
        ("ＥＮＯＵＧＨ", None, "enough"),
        # Video decorations.
        ("ENOUGH (Official Video)", None, "enough"),
        ("ENOUGH [Official Music Video]", None, "enough"),
        ("страх (Lyrics)", None, "страх"),
        ("страх (Lyric Video)", None, "страх"),
        ("ENOUGH | darkside of love", None, "enough"),
        ("Miracle way - Remastered", None, "miracle way"),
        ("Miracle way - 2011 Remaster", None, "miracle way"),
        # Official-channel prefix, only when asked for.
        ("PERSONAL ANIMOSITY - ENOUGH (Official Video)", ARTIST, "enough"),
        ("Personal Animosity – страх", ARTIST, "страх"),
        ("PERSONAL ANIMOSITY - ENOUGH", None, "personal animosity enough"),
        # Kept: parts that make a different track.
        ("PERSON (Outro)", None, "person outro"),
        ("Arcade Gusli (Bonus Track)", None, "arcade gusli bonus track"),
        ("haziness (Preview)", None, "haziness preview"),
        ("April&May", None, "april may"),
    ]

    def test_table(self):
        for title, artist, expected in self.CASES:
            with self.subTest(title=title):
                self.assertEqual(normalize(title, artist), expected)


class Match(unittest.TestCase):
    def test_several_videos_per_track(self):
        titles = ["ENOUGH", "страх"]
        videos = [V("a", "ENOUGH", False), V("b", "ENOUGH", False),
                  V("c", "PERSONAL ANIMOSITY - ENOUGH (Official Video)", True)]
        per_row, unmatched = match_videos(titles, videos, ARTIST)
        self.assertEqual([v.id for v in per_row[0]], ["a", "b", "c"])
        self.assertEqual(per_row[1], [])
        self.assertEqual(unmatched, [])

    def test_duplicate_catalog_titles_go_to_first_row(self):
        # "Tape and Neon" is on both "dcw" and "Junior Forever".
        titles = ["Tape and Neon", "Other", "Tape and Neon"]
        per_row, _ = match_videos(titles, [V("a", "Tape and Neon", False)], ARTIST)
        self.assertEqual([len(r) for r in per_row], [1, 0, 0])

    def test_unmatched_videos_are_returned(self):
        videos = [V("a", "Garden Party", False), V("b", "haziness (Preview)", False)]
        per_row, unmatched = match_videos(["haziness"], videos, ARTIST)
        self.assertEqual(per_row, [[]])
        self.assertEqual([v.id for v in unmatched], ["a", "b"])

    def test_artist_prefix_not_stripped_from_topic_videos(self):
        # A Topic video is titled by the track alone; never strip a prefix there.
        videos = [V("a", "PERSONAL ANIMOSITY - ENOUGH", False)]
        per_row, unmatched = match_videos(["ENOUGH"], videos, ARTIST)
        self.assertEqual(per_row, [[]])
        self.assertEqual(len(unmatched), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
