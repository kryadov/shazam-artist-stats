#!/usr/bin/env python3
"""Offline tests for youtube — fake YouTube Data API responses, no network."""

import json
import unittest
from unittest import mock

import youtube as yt

KEY = "AIzaSECRET"
TOPIC_ID = "UCW0XqjvAVf_zimSmZ9MzTaA"  # PERSONAL ANIMOSITY - Topic
EMPTY_TOPIC_ID = "UCExcanjCPOj2O8CKzHas8Fg"  # same title, no public uploads


def api_error(status, reason, message="boom", url="https://x/videos?key=" + KEY):
    body = json.dumps({"error": {"code": status, "message": message,
                                 "errors": [{"reason": reason}]}})
    return yt._parse_error(status, body, url)


def video_item(vid, title, views, likes=None):
    stats = {"viewCount": str(views)}
    if likes is not None:
        stats["likeCount"] = str(likes)
    return {"id": vid, "snippet": {"title": title, "channelId": TOPIC_ID},
            "statistics": stats}


class Fake:
    """Routes calls by endpoint name and records the URLs that were requested."""

    def __init__(self, routes):
        self.routes = routes
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        op = url.split("/v3/", 1)[1].split("?", 1)[0]
        result = self.routes[op](url)
        if isinstance(result, Exception):
            raise result
        return result


class Redaction(unittest.TestCase):
    def test_key_is_stripped(self):
        url = f"https://www.googleapis.com/youtube/v3/videos?id=a&key={KEY}&part=x"
        self.assertNotIn(KEY, yt.redact(url))
        self.assertIn("key=REDACTED&part=x", yt.redact(url))

    def test_error_message_has_no_key(self):
        client = yt.Client(KEY, retries=1, backoff=0)
        fake = Fake({"videos": lambda url: yt._parse_error(
            403, json.dumps({"error": {"message": "nope",
                                       "errors": [{"reason": "forbidden"}]}}), url)})
        with mock.patch.object(yt, "_fetch", fake):
            with self.assertRaises(yt.ApiError) as ctx:
                client.get_videos(["a"])
        self.assertIn(KEY, fake.urls[0])  # the request itself carries the key
        self.assertNotIn(KEY, str(ctx.exception))


class Errors(unittest.TestCase):
    def test_quota_exceeded_is_not_retried_and_has_hint(self):
        client = yt.Client(KEY, retries=3, backoff=0)
        fake = Fake({"videos": lambda url: api_error(403, "quotaExceeded")})
        with mock.patch.object(yt, "_fetch", fake):
            with self.assertRaises(yt.ApiError) as ctx:
                client.get_videos(["a"])
        self.assertEqual(len(fake.urls), 1)
        self.assertIn("quotaExceeded", str(ctx.exception))
        self.assertIn("Hint:", str(ctx.exception))

    def test_api_not_enabled_hint(self):
        err = api_error(403, "accessNotConfigured")
        self.assertIn("enable \"YouTube Data API v3\"", str(err))

    def test_invalid_key_reason_from_details(self):
        body = json.dumps({"error": {"code": 400, "message": "API key not valid.",
                                     "errors": [{"reason": "badRequest"}],
                                     "details": [{"reason": "API_KEY_INVALID"}]}})
        err = yt._parse_error(400, body, "https://x?key=" + KEY)
        self.assertEqual(err.reasons, ["badRequest", "API_KEY_INVALID"])
        self.assertIn("check YOUTUBE_API_KEY", str(err))

    def test_5xx_and_network_errors_are_retried(self):
        client = yt.Client(KEY, retries=3, backoff=0)
        answers = iter([api_error(503, "backendError"),
                        yt.YouTubeError("Network error"),
                        {"items": [video_item("a", "ENOUGH", 5, 1)]}])
        fake = Fake({"videos": lambda url: next(answers)})
        with mock.patch.object(yt, "_fetch", fake):
            videos = client.get_videos(["a"])
        self.assertEqual(len(fake.urls), 3)
        self.assertEqual(videos[0].views, 5)


class Uploads(unittest.TestCase):
    def test_pagination(self):
        pages = {
            None: {"items": [{"contentDetails": {"videoId": "a"}},
                             {"contentDetails": {"videoId": "b"}}],
                   "nextPageToken": "P2"},
            "P2": {"items": [{"contentDetails": {"videoId": "c"}}]},
        }

        def playlist_items(url):
            token = url.split("pageToken=")[1].split("&")[0] if "pageToken=" in url else None
            return pages[token]

        client = yt.Client(KEY, retries=1, backoff=0)
        with mock.patch.object(yt, "_fetch", Fake({"playlistItems": playlist_items})):
            self.assertEqual(client.list_uploads("UU" + TOPIC_ID[2:]), ["a", "b", "c"])

    def test_missing_uploads_playlist_is_empty(self):
        client = yt.Client(KEY, retries=1, backoff=0)
        fake = Fake({"playlistItems": lambda url: api_error(404, "playlistNotFound")})
        with mock.patch.object(yt, "_fetch", fake):
            self.assertEqual(client.list_uploads("UU" + EMPTY_TOPIC_ID[2:]), [])

    def test_videos_batched_by_50_and_hidden_likes(self):
        ids = [f"v{i}" for i in range(120)]
        seen_batches = []

        def videos(url):
            batch = url.split("id=")[1].split("&")[0].split("%2C")
            seen_batches.append(len(batch))
            return {"items": [video_item(v, v, 10, None if v == "v0" else 2)
                              for v in batch]}

        client = yt.Client(KEY, retries=1, backoff=0)
        with mock.patch.object(yt, "_fetch", Fake({"videos": videos})):
            out = client.get_videos(ids)
        self.assertEqual(seen_batches, [50, 50, 20])
        self.assertEqual(len(out), 120)
        self.assertIsNone(out[0].likes)
        self.assertEqual(out[1].likes, 2)
        self.assertEqual(client.units, 3)


class Channels(unittest.TestCase):
    def test_topic_search_keeps_exact_titles_only(self):
        result = {"items": [
            {"id": {"channelId": EMPTY_TOPIC_ID},
             "snippet": {"title": "PERSONAL ANIMOSITY - Topic"}},
            {"id": {"channelId": "UCjoeqx7tijyz_3mwXftyrSw"},
             "snippet": {"title": "Animosity - Topic"}},
            {"id": {"channelId": TOPIC_ID},
             "snippet": {"title": "PERSONAL ANIMOSITY - Topic"}},
        ]}
        client = yt.Client(KEY, retries=1, backoff=0)
        with mock.patch.object(yt, "_fetch", Fake({"search": lambda url: result})):
            ids = client.find_topic_channels("Personal Animosity")
        self.assertEqual(ids, [EMPTY_TOPIC_ID, TOPIC_ID])
        self.assertEqual(client.units, yt.SEARCH_COST)

    def test_get_channels_ids_handles_and_urls(self):
        def channels(url):
            if "forHandle=" in url:
                return {"items": [{"id": "UC" + "o" * 22, "snippet": {"title": "Official"},
                                   "contentDetails": {"relatedPlaylists": {"uploads": "UUo"}}}]}
            return {"items": [{"id": TOPIC_ID,
                               "snippet": {"title": "PERSONAL ANIMOSITY - Topic"},
                               "contentDetails": {"relatedPlaylists": {
                                   "uploads": "UU" + TOPIC_ID[2:]}}}]}

        client = yt.Client(KEY, retries=1, backoff=0)
        with mock.patch.object(yt, "_fetch", Fake({"channels": channels})):
            topic = client.get_channels([f"https://www.youtube.com/channel/{TOPIC_ID}"])
            official = client.get_channels(["@personalanimosity"], official=True)
        self.assertEqual(topic[0].uploads, "UU" + TOPIC_ID[2:])
        self.assertFalse(topic[0].official)
        self.assertTrue(official[0].official)

    def test_unknown_channel_fails(self):
        client = yt.Client(KEY, retries=1, backoff=0)
        with mock.patch.object(yt, "_fetch", Fake({"channels": lambda url: {"items": []}})):
            with self.assertRaises(yt.YouTubeError):
                client.get_channels([TOPIC_ID])
            with self.assertRaises(yt.YouTubeError):
                client.get_channels(["@nobody"])
        with self.assertRaises(yt.YouTubeError):
            client.get_channels(["not a channel"])

    def test_collect_videos_dedupes_across_channels(self):
        client = yt.Client(KEY, retries=1, backoff=0)
        chans = [yt.Channel("UC1", "T", "UU1"), yt.Channel("UC2", "O", "UU2", official=True),
                 yt.Channel("UC3", "Empty", "")]
        uploads = {"UU1": ["a", "b"], "UU2": ["b", "c"]}
        with mock.patch.object(client, "list_uploads", side_effect=lambda p: uploads[p]), \
             mock.patch.object(client, "get_videos",
                               side_effect=lambda ids, official: [
                                   yt.Video(i, i, "", 1, 1, official) for i in ids]):
            videos = client.collect_videos(chans)
        self.assertEqual([(v.id, v.official) for v in videos],
                         [("a", False), ("b", False), ("c", True)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
