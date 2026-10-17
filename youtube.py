"""YouTube Data API v3 client: channels, uploads and video statistics.

Only the official API is used. Quota cost per call: `search.list` 100 units,
everything else 1 unit. A full catalog costs about 20 units plus 100 for the
Topic channel search, out of a default 10,000/day.
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Optional

API = "https://www.googleapis.com/youtube/v3/{op}"
SEARCH_COST = 100
PAGE = 50  # max ids/items per call for playlistItems.list and videos.list

KEY_RE = re.compile(r"([?&]key=)[^&#\s]*")
CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")
CHANNEL_URL_RE = re.compile(r"youtube\.com/(?:channel/(UC[\w-]{22})|(@[\w.-]+))")

# Hints for API errors that retrying can't fix.
HINTS = {
    "keyInvalid": "check YOUTUBE_API_KEY in .env",
    "API_KEY_INVALID": "check YOUTUBE_API_KEY in .env",
    "quotaExceeded": "the daily quota (10,000 units) is used up; it resets at "
                     "midnight Pacific time",
    "dailyLimitExceeded": "the daily quota is used up; it resets at midnight "
                          "Pacific time",
    "accessNotConfigured": "enable \"YouTube Data API v3\" for the key's Google "
                           "Cloud project",
    "SERVICE_DISABLED": "enable \"YouTube Data API v3\" for the key's Google "
                        "Cloud project",
    "API_KEY_SERVICE_BLOCKED": "the key's API restrictions don't allow "
                               "YouTube Data API v3",
    "ipRefererBlocked": "the key's application restrictions block this request",
}


class YouTubeError(Exception):
    pass


class ApiError(YouTubeError):
    """An HTTP error answered by the API, with the reasons from its JSON body."""

    def __init__(self, status: int, reasons: list, message: str, url: str):
        self.status = status
        self.reasons = reasons
        hint = next((HINTS[r] for r in reasons if r in HINTS), None)
        text = f"YouTube API HTTP {status}"
        if reasons:
            text += f" ({', '.join(reasons)})"
        text += f": {message or 'no details'} [{redact(url)}]"
        if hint:
            text += f"\nHint: {hint}."
        super().__init__(text)


@dataclass
class Channel:
    id: str
    title: str
    uploads: str  # uploads playlist id ("" if the channel has none)
    official: bool = False  # official artist channel, not an auto-generated Topic


@dataclass
class Video:
    id: str
    title: str
    channel_id: str
    views: int
    likes: Optional[int]  # None: the uploader hides likes
    official: bool = False


def redact(text: str) -> str:
    """Strip the API key from a URL before it appears in an error or log."""
    return KEY_RE.sub(r"\1REDACTED", text)


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _parse_error(status: int, body: str, url: str) -> ApiError:
    reasons, message = [], ""
    try:
        err = json.loads(body).get("error", {})
        message = err.get("message", "")
        # v3 puts reasons in "errors"; newer Google errors also in "details".
        for item in err.get("errors", []) + err.get("details", []):
            reason = item.get("reason")
            if reason and reason not in reasons:
                reasons.append(reason)
    except (ValueError, AttributeError):
        message = body[:200]
    return ApiError(status, reasons, re.sub(r"<[^>]+>", "", message), url)


def _fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise _parse_error(e.code, e.read().decode("utf-8", "replace"), url) from None
    except urllib.error.URLError as e:
        raise YouTubeError(f"Network error for {redact(url)}: {e.reason}") from None
    except ValueError as e:
        raise YouTubeError(f"Could not parse YouTube response from "
                           f"{redact(url)}: {e}") from None


class Client:
    def __init__(self, key: str, retries: int = 3, backoff: float = 1.5):
        self.key = key
        self.retries = retries
        self.backoff = backoff
        self.units = 0  # quota spent, reported in the progress output

    def call(self, op: str, params: dict, cost: int = 1) -> dict:
        """One API call. 4xx fails at once (with a hint); 5xx/network retry."""
        url = API.format(op=op) + "?" + urllib.parse.urlencode({**params, "key": self.key})
        for attempt in range(1, self.retries + 1):
            self.units += cost
            try:
                return _fetch(url)
            except ApiError as e:
                if e.status < 500 or attempt == self.retries:
                    raise
            except YouTubeError:
                if attempt == self.retries:
                    raise
            time.sleep(self.backoff * attempt)
        raise AssertionError("unreachable")

    # ----------------------------------------------------------------------- #
    # Channels
    # ----------------------------------------------------------------------- #
    def find_topic_channels(self, artist: str) -> list:
        """Ids of channels titled exactly "<artist> - Topic" (one search.list).

        An artist can have several Topic channels with the same title, like
        duplicate Apple artist profiles, so every exact match is returned.
        """
        want = f"{artist} - Topic".casefold()
        data = self.call("search", {"part": "snippet", "type": "channel",
                                    "q": f"{artist} - Topic", "maxResults": 10},
                         cost=SEARCH_COST)
        ids = []
        for item in data.get("items", []):
            cid = item.get("id", {}).get("channelId") or item.get("snippet", {}).get("channelId")
            title = item.get("snippet", {}).get("title", "")
            if cid and title.strip().casefold() == want and cid not in ids:
                ids.append(cid)
        return ids

    def get_channels(self, refs: list, official: bool = False) -> list:
        """Resolve channel ids, @handles or channel URLs to Channel objects."""
        ids, handles = [], []
        for ref in refs:
            ref = ref.strip()
            m = CHANNEL_URL_RE.search(ref)
            if m:
                ref = m.group(1) or m.group(2)
            if CHANNEL_ID_RE.match(ref):
                ids.append(ref)
            elif ref.startswith("@"):
                handles.append(ref)
            else:
                raise YouTubeError(f"Not a YouTube channel id or @handle: \"{ref}\".")

        found = {}
        queries = ([{"id": ",".join(ids)}] if ids else []) + \
                  [{"forHandle": h} for h in handles]
        for q in queries:
            data = self.call("channels", {"part": "snippet,contentDetails", **q})
            items = data.get("items", [])
            if not items and "forHandle" in q:
                raise YouTubeError(f"YouTube channel not found: {q['forHandle']}")
            for it in items:
                found[it["id"]] = Channel(
                    id=it["id"],
                    title=it.get("snippet", {}).get("title", it["id"]),
                    uploads=it.get("contentDetails", {})
                              .get("relatedPlaylists", {}).get("uploads", ""),
                    official=official,
                )
        missing = [i for i in ids if i not in found]
        if missing:
            raise YouTubeError(f"YouTube channel not found: {', '.join(missing)}")
        return list(found.values())

    # ----------------------------------------------------------------------- #
    # Videos
    # ----------------------------------------------------------------------- #
    def list_uploads(self, playlist_id: str) -> list:
        """All video ids in an uploads playlist, 50 per page.

        A channel without public uploads answers 404 playlistNotFound; that is
        an empty channel, not an error.
        """
        ids, token = [], None
        while True:
            params = {"part": "contentDetails", "playlistId": playlist_id,
                      "maxResults": PAGE}
            if token:
                params["pageToken"] = token
            try:
                data = self.call("playlistItems", params)
            except ApiError as e:
                if e.status == 404 and "playlistNotFound" in e.reasons:
                    return ids
                raise
            for it in data.get("items", []):
                vid = it.get("contentDetails", {}).get("videoId")
                if vid:
                    ids.append(vid)
            token = data.get("nextPageToken")
            if not token:
                return ids

    def get_videos(self, ids: list, official: bool = False) -> list:
        """Title and statistics, 50 ids per call. Private/deleted ids are absent."""
        out = []
        for start in range(0, len(ids), PAGE):
            data = self.call("videos", {"part": "snippet,statistics",
                                        "id": ",".join(ids[start:start + PAGE]),
                                        "maxResults": PAGE})
            for it in data.get("items", []):
                st = it.get("statistics", {})
                likes = st.get("likeCount")
                out.append(Video(
                    id=it["id"],
                    title=it.get("snippet", {}).get("title", ""),
                    channel_id=it.get("snippet", {}).get("channelId", ""),
                    views=int(st.get("viewCount", 0)),
                    likes=int(likes) if likes is not None else None,
                    official=official,
                ))
        return out

    def collect_videos(self, channels: list) -> list:
        """Every upload of every channel, each video once."""
        seen = set()
        videos = []
        for ch in channels:
            ids = [v for v in (self.list_uploads(ch.uploads) if ch.uploads else [])
                   if v not in seen]
            seen.update(ids)
            videos += self.get_videos(ids, official=ch.official)
        return videos
