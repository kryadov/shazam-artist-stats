#!/usr/bin/env python3
"""Offline tests for shazam_stats — no network access required."""

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import shazam_stats as s
import youtube as yt
from config import Config, ConfigError

# Shortened copy of the F5/Shape interstitial shazam.com serves with HTTP 200.
CHALLENGE_HTML = (
    '<!DOCTYPE html><html lang="en"><head><title>Client Challenge</title></head>'
    '<body><noscript>JavaScript is disabled in your browser.</noscript></body></html>'
)

# Shape of the real song page (classes are hashed per build, hence the suffixes).
SONG_HTML = (
    '<html><head><title>Blinding Lights - The Weeknd: Song Lyrics</title></head><body>'
    '<span class="TrackMetadata_item__4py4X"><svg class="TrackMetadata_icon__Rzl5C" '
    'style="width:18px;height:18px" xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink"><use xlink:href="#ShazamLogo"></use>'
    '</svg><span>32,221,476</span></span></body></html>'
)

# Song-page markup from older builds.
LEGACY_SONG_HTML = (
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


class TransientErrors(unittest.TestCase):
    """shazam.com sporadically 404s valid pages; one bad track must not abort a run."""

    def test_get_retries_http_errors(self):
        effects = [s.ShazamError("HTTP 404 for x"), SONG_HTML]
        with mock.patch.object(s, "_fetch", side_effect=effects):
            body = s._get("https://www.shazam.com/en-US/song/1/x", retries=2, backoff=0)
        self.assertIs(body, SONG_HTML)

    def test_get_raises_http_error_after_retries(self):
        with mock.patch.object(s, "_fetch", side_effect=s.ShazamError("HTTP 404 for x")):
            with self.assertRaises(s.ShazamError) as ctx:
                s._get("https://www.shazam.com/en-US/song/1/x", retries=2, backoff=0)
        self.assertIn("404", str(ctx.exception))

    def test_failed_track_is_skipped_and_reported(self):
        def fake_count(sid, path, album):
            if sid == "2":
                raise s.ShazamError("HTTP 404")
            return s.Track(sid, "T" + sid, album, 10)

        with mock.patch.object(s, "get_artist_albums", return_value=("A", [("9", "Alb")])),              mock.patch.object(s, "get_album_song_links",
                               return_value=[("1", "/song/1/a"), ("2", "/song/2/b")]),              mock.patch.object(s, "get_song_count", side_effect=fake_count):
            stats = s.collect_stats("42", "", "us", limit=100, workers=1, verbose=False)
        self.assertEqual(stats.track_count, 1)
        self.assertEqual(stats.total_shazams, 10)
        self.assertEqual(stats.failed, ["/song/2/b"])
        self.assertIn("could not be fetched", s.render_table(stats))


class Parsing(unittest.TestCase):
    def test_count_and_title_parsed(self):
        with mock.patch.object(s, "_fetch", return_value=SONG_HTML):
            track = s.get_song_count("1488408568", "/song/1488408568/blinding-lights",
                                     "After Hours")
        self.assertEqual(track.shazam_count, 32_221_476)
        self.assertEqual(track.title, "Blinding Lights")
        self.assertEqual(track.album, "After Hours")

    def test_legacy_count_markup_parsed(self):
        with mock.patch.object(s, "_fetch", return_value=LEGACY_SONG_HTML):
            track = s.get_song_count("1", "/song/1/x", "Album")
        self.assertEqual(track.shazam_count, 32_221_476)


# --------------------------------------------------------------------------- #
# YouTube integration
# --------------------------------------------------------------------------- #
def make_stats(sources=("shazam",)):
    # Catalog order; "ENOUGH" appears on two releases.
    tracks = [s.Track("1", "ENOUGH", "darkside of love - Single", 594),
              s.Track("2", "страх", "страх - Single", 389),
              s.Track("3", "ENOUGH", "Compilation", 5)]
    return s.ArtistStats("1720235535", "PERSONAL ANIMOSITY", 988, 3, tracks,
                         sources=list(sources))


def topic_video(vid, title, views, likes):
    return yt.Video(vid, title, "UCW0XqjvAVf_zimSmZ9MzTaA", views, likes)


TOPIC = yt.Channel("UCW0XqjvAVf_zimSmZ9MzTaA", "PERSONAL ANIMOSITY - Topic",
                   "UUW0XqjvAVf_zimSmZ9MzTaA")


def table_header(out):
    return next(line for line in out.splitlines() if line.lstrip().startswith("#"))


class AddYouTube(unittest.TestCase):
    def setUp(self):
        self.stats = make_stats(("shazam", "youtube"))
        client = mock.Mock(units=16)
        client.collect_videos.return_value = [
            topic_video("a", "ENOUGH", 1000, 20),    # Art Track from the single
            topic_video("b", "ENOUGH", 83, None),    # same track, likes hidden
            topic_video("c", "Garden Party", 6, 0),  # from another artist profile
        ]
        s.add_youtube(self.stats, client, [TOPIC], verbose=False)

    def test_videos_are_summed_on_first_duplicate_row(self):
        first, scary, dup = self.stats.tracks
        self.assertEqual((first.youtube_views, first.youtube_likes), (1083, 20))
        self.assertEqual(first.youtube_videos, ["a", "b"])
        self.assertEqual((scary.youtube_views, scary.youtube_videos), (0, []))
        self.assertEqual(dup.youtube_videos, [])

    def test_totals_and_unmatched(self):
        st = self.stats
        self.assertEqual((st.total_youtube_views, st.total_youtube_likes), (1083, 20))
        self.assertEqual(st.youtube_tracks_matched, 1)
        self.assertEqual(st.youtube_hidden_likes, 1)
        self.assertEqual(st.unmatched_videos,
                         [{"id": "c", "title": "Garden Party", "views": 6}])
        self.assertEqual(st.youtube_channels[0]["title"], "PERSONAL ANIMOSITY - Topic")

    def test_table_has_shazam_and_youtube_columns(self):
        out = s.render_table(self.stats)
        header = table_header(out)
        self.assertIn("Shazams", header)
        self.assertIn("YouTube (views / likes)", header)
        self.assertIn("1 083 / 20", out)
        self.assertIn("—", out)
        self.assertIn("YouTube (PERSONAL ANIMOSITY - Topic): 1 083 views, 20 likes, "
                      "1/3 tracks matched", out)
        self.assertIn("hide their likes", out)
        self.assertIn("Unmatched YouTube videos (1", out)
        self.assertIn("Garden Party", out)

    def test_itunes_titles_replace_lossy_shazam_titles(self):
        st = make_stats(("shazam", "youtube"))
        st.tracks[1].title = "NN"  # Shazam's page title for "NN (MIXED)"
        client = mock.Mock(units=1)
        client.collect_videos.return_value = [topic_video("m", "NN (MIXED)", 295, 3)]
        s.add_youtube(st, client, [TOPIC], verbose=False, titles={"2": "NN (MIXED)"})
        self.assertEqual(st.tracks[1].title, "NN (MIXED)")
        self.assertEqual(st.tracks[1].youtube_videos, ["m"])
        self.assertEqual(st.tracks[0].title, "ENOUGH")  # not in the map: kept

    def test_json_fields(self):
        data = json.loads(s.render_json(self.stats))
        self.assertEqual(data["total_youtube_views"], 1083)
        self.assertEqual(data["tracks"][0]["youtube_videos"], ["a", "b"])
        self.assertEqual(data["tracks"][0]["shazam_count"], 594)


class TableColumns(unittest.TestCase):
    def test_shazam_only(self):
        out = s.render_table(make_stats(("shazam",)))
        self.assertIn("Shazams", table_header(out))
        self.assertNotIn("YouTube", out)

    def test_youtube_only(self):
        st = make_stats(("youtube",))
        st.total_shazams = None
        for t in st.tracks:
            t.shazam_count = None
            t.youtube_views, t.youtube_likes, t.youtube_videos = 5, 1, ["x"]
        st.total_youtube_views = st.total_youtube_likes = 0
        st.youtube_tracks_matched = 3
        out = s.render_table(st)
        self.assertNotIn("Shazams", table_header(out))
        self.assertIn("YouTube (views / likes)", table_header(out))
        self.assertIn("Tracks: 3", out)

    def test_long_unmatched_list_is_truncated(self):
        st = make_stats(("shazam", "youtube"))
        st.total_youtube_views = st.total_youtube_likes = st.youtube_tracks_matched = 0
        st.unmatched_videos = [{"id": str(i), "title": f"V{i}", "views": 1}
                               for i in range(s.UNMATCHED_SHOWN + 5)]
        self.assertIn("... and 5 more", s.render_table(st))


class Sorting(unittest.TestCase):
    def stats(self):
        st = make_stats(("shazam", "youtube"))
        for t, (views, likes) in zip(st.tracks, [(10, 9), (500, 1), (0, 0)]):
            t.youtube_views, t.youtube_likes = views, likes
        return st

    def test_sort_by_each_key(self):
        for key, expected in [("shazam", ["1", "2", "3"]), ("views", ["2", "1", "3"]),
                              ("likes", ["1", "2", "3"])]:
            st = self.stats()
            s.sort_tracks(st, key)
            self.assertEqual([t.song_id for t in st.tracks], expected, key)

    def test_uncollected_sorts_last(self):
        st = self.stats()
        st.tracks[0].youtube_views = None
        s.sort_tracks(st, "views")
        self.assertEqual(st.tracks[-1].song_id, "1")


class ItunesTracks(unittest.TestCase):
    def test_tracks_from_lookup_first_album_wins(self):
        data = {"results": [
            {"wrapperType": "collection", "collectionId": 2},
            {"wrapperType": "track", "collectionId": 2, "trackId": 11,
             "trackName": "Tape and Neon", "trackNumber": 1},
            {"wrapperType": "track", "collectionId": 1, "trackId": 11,
             "trackName": "Tape and Neon", "trackNumber": 3},
            {"wrapperType": "track", "collectionId": 1, "trackId": 12,
             "trackName": "Our youth", "trackNumber": 1},
        ]}
        with mock.patch.object(s, "_itunes", return_value=data):
            tracks = s.get_itunes_tracks([("1", "dcw"), ("2", "Junior Forever")], "us")
        self.assertEqual(tracks, [("12", "Our youth", "dcw"),
                                  ("11", "Tape and Neon", "dcw")])


class MainFlow(unittest.TestCase):
    """main() with every network step mocked; never reads the real .env."""

    CATALOG = ("PERSONAL ANIMOSITY", [("9", "darkside of love - Single")])

    def run_main(self, cfg, argv=(), collect_videos=None):
        client = mock.Mock(units=1)
        client.collect_videos.side_effect = collect_videos or (
            lambda channels: [topic_video("a", "ENOUGH", 1000, 20)])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(s, "load_config", return_value=cfg), \
             mock.patch.object(s, "get_artist_albums", return_value=self.CATALOG), \
             mock.patch.object(s, "get_album_song_links",
                               return_value=[("1", "/song/1/enough")]) as links, \
             mock.patch.object(s, "get_song_count",
                               return_value=s.Track("1", "ENOUGH", "darkside", 594)), \
             mock.patch.object(s, "get_itunes_tracks",
                               return_value=[("1", "ENOUGH", "darkside")]), \
             mock.patch.object(yt, "Client", return_value=client), \
             mock.patch.object(s, "resolve_youtube_channels", return_value=[TOPIC]), \
             redirect_stdout(out), redirect_stderr(err):
            code = s.main(["--artist-id", "1720235535", "-q", *argv])
        return code, out.getvalue(), err.getvalue(), links

    def test_both_sources(self):
        code, out, _, _ = self.run_main(Config(["shazam", "youtube"], "k"))
        self.assertEqual(code, 0)
        self.assertIn("594", out)
        self.assertIn("1 000 / 20", out)

    def test_youtube_only_skips_shazam_pages(self):
        code, out, _, links = self.run_main(Config(["youtube"], "k"))
        self.assertEqual(code, 0)
        links.assert_not_called()
        self.assertNotIn("Shazams", out)
        self.assertIn("1 000 / 20", out)

    def test_youtube_failure_keeps_shazam_results(self):
        def boom(channels):
            raise yt.YouTubeError("YouTube API HTTP 403 (quotaExceeded)")
        code, out, err, _ = self.run_main(Config(["shazam", "youtube"], "k"),
                                          collect_videos=boom)
        self.assertEqual(code, 0)
        self.assertIn("Shazams", out)
        self.assertNotIn("YouTube", out)
        self.assertIn("YouTube statistics skipped", err)

    def test_youtube_only_failure_is_an_error(self):
        def boom(channels):
            raise yt.YouTubeError("quotaExceeded")
        code, _, err, _ = self.run_main(Config(["youtube"], "k"), collect_videos=boom)
        self.assertEqual(code, 1)
        self.assertIn("quotaExceeded", err)

    def test_config_error_before_any_request(self):
        err = io.StringIO()
        with mock.patch.object(s, "load_config", side_effect=ConfigError("no key")), \
             mock.patch.object(s, "get_artist_albums") as albums, redirect_stderr(err):
            code = s.main(["--artist-id", "1", "--sources", "youtube"])
        self.assertEqual(code, 1)
        albums.assert_not_called()
        self.assertIn("no key", err.getvalue())

    def test_sort_flag_needs_its_source(self):
        with mock.patch.object(s, "load_config", return_value=Config(["shazam"])), \
             redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                s.main(["--artist-id", "1", "--sort", "views"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
