#!/usr/bin/env python3
"""Offline tests for shazam_stats — no network access required."""

import unittest
from unittest import mock

import shazam_stats as s

# Shortened copy of the F5/Shape interstitial shazam.com serves with HTTP 200.
CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en"><head><title>Client Challenge</title></head>'
    '<body><noscript>JavaScript is disabled in your browser.</noscript></body></html>'
)

# Shape of the real song page (classes are hashed per build, hence the suffixes).
SONG_HTML = (
    '<html><head><title>Blinding Lights - The Weeknd: Song Lyrics</title></head><body>'
    '<span class="Text-module_x__a NewTrackPageHeader_tagCountText__KqU_q '
    'Text-post-module_size-base__o1">32,221,476</span></body></html>'
)


class UrlShape(unittest.TestCase):
    """Locale-prefixed paths are the ones that are not bot-challenged."""

    def test_song_url_is_locale_prefixed(self):
        self.assertEqual(s.song_url("/song/1488408568/blinding-lights"),
                         "https://www.shazam.com/en-US/song/1488408568/blinding-lights")

    def test_album_url_is_locale_prefixed(self):
        self.assertEqual(s.album_url("1750556411"),
                         "https://www.shazam.com/en-US/album/1750556411/x")


class ChallengeDetection(unittest.TestCase):
    """A challenge must fail loudly, never be parsed as a zero-count track."""

    def test_challenge_body_is_recognised(self):
        self.assertTrue(s.is_challenge(CHALLENGE_HTML))

    def test_real_page_is_not_a_challenge(self):
        self.assertFalse(s.is_challenge(SONG_HTML))

    def test_get_raises_on_challenge(self):
        with mock.patch.object(s, "_fetch", return_value=CHALLENGE_HTML):
            with self.assertRaises(s.ShazamError) as ctx:
                s._get("https://www.shazam.com/en-US/song/1/x", retries=2, backoff=0)
        self.assertIn("challenge", str(ctx.exception).lower())

    def test_get_retries_then_succeeds(self):
        bodies = [CHALLENGE_HTML, SONG_HTML]
        with mock.patch.object(s, "_fetch", side_effect=bodies):
            body = s._get("https://www.shazam.com/en-US/song/1/x", retries=2, backoff=0)
        self.assertIs(body, SONG_HTML)

    def test_song_count_never_silently_zero_on_challenge(self):
        with mock.patch.object(s, "_fetch", return_value=CHALLENGE_HTML):
            with self.assertRaises(s.ShazamError):
                s.get_song_count("1", "/song/1/x", "Album", retries=1, backoff=0)


class Parsing(unittest.TestCase):
    """The song-page markup itself is unchanged; parsing must keep working."""

    def test_count_and_title_parsed(self):
        with mock.patch.object(s, "_fetch", return_value=SONG_HTML):
            track = s.get_song_count("1488408568", "/song/1488408568/blinding-lights",
                                     "After Hours")
        self.assertEqual(track.shazam_count, 32_221_476)
        self.assertEqual(track.title, "Blinding Lights")
        self.assertEqual(track.album, "After Hours")


if __name__ == "__main__":
    unittest.main(verbosity=2)
