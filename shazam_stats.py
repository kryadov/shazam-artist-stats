#!/usr/bin/env python3
"""shazam-artist-stats — Shazam statistics for a given artist.

Data source: public shazam.com pages (no API keys required):
  * the artist page server-renders the artist's albums and singles;
  * each album page server-renders its track list;
  * each song page server-renders its Shazam ("tag") count.

Artist lookup by name uses Shazam's internal amapi search endpoint with a
token obtained dynamically (the same flow the website itself uses).

Examples:
    python shazam_stats.py "Personal Animosity"
    python shazam_stats.py --artist-id 1720235535
    python shazam_stats.py "Personal Animosity" --json
    python shazam_stats.py --artist-id 1720235535 --limit 200
"""

import argparse
import gzip
import json
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict
from html import unescape
from typing import Optional

BASE = "https://www.shazam.com"
TOKEN_URL = BASE + "/services/partner/oauth/commerce/validate"
SEARCH_URL = BASE + "/services/amapi/v1/catalog/{country}/search"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

# Links found on artist / album pages.
SONG_LINK_RE = re.compile(r'/song/(\d+)/([a-z0-9][a-z0-9-]*)')
ALBUM_LINK_RE = re.compile(r'/album/(\d+)/([a-z0-9][a-z0-9-]*)')
# Shazam count on a song page: <span class="...tagCountText...">123</span>
COUNT_RE = re.compile(r'tagCountText[^"]*">\s*([\d,]+)\s*<')
TITLE_RE = re.compile(r'<title>([^<]+)</title>', re.I)
OG_TITLE_RE = re.compile(r'<meta\s+property="og:title"\s+content="([^"]+)"', re.I)


class ShazamError(Exception):
    pass


@dataclass
class Track:
    song_id: str
    title: str
    album: str
    shazam_count: int


@dataclass
class ArtistStats:
    artist_id: str
    artist_name: str
    total_shazams: int
    track_count: int
    tracks: list  # list[Track]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def _request(url: str, *, token: Optional[str] = None) -> str:
    headers = {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, identity",
        "Origin": BASE,
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(url, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except urllib.error.HTTPError as e:
        raise ShazamError(f"HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise ShazamError(f"Network error for {url}: {e.reason}") from e
    raw = resp.read()
    if resp.headers.get("Content-Encoding") == "gzip":
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


def get_token() -> str:
    """Dynamic developer token (the x-shz-validation response header)."""
    headers = {"User-Agent": UA, "Accept": "*/*", "Origin": BASE}
    req = urllib.request.Request(TOKEN_URL, headers=headers)
    try:
        resp = urllib.request.urlopen(req, timeout=30)
    except (urllib.error.HTTPError, urllib.error.URLError) as e:
        raise ShazamError(f"Could not obtain Shazam token: {e}") from e
    token = resp.headers.get("x-shz-validation")
    if not token:
        raise ShazamError("Response is missing the x-shz-validation token header.")
    return token


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #
def search_artist(name: str, country: str):
    """Return matches ordered by relevance: [{'id': ..., 'name': ...}, ...]."""
    token = get_token()
    q = urllib.parse.urlencode({"types": "artists", "term": name, "limit": 10})
    url = SEARCH_URL.format(country=country) + "?" + q
    body = _request(url, token=token)
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        raise ShazamError(f"Could not parse search response: {e}") from e
    items = (data.get("results", {}).get("artists", {}) or {}).get("data", []) or []
    out = []
    for it in items:
        aid = it.get("id")
        nm = (it.get("attributes") or {}).get("name", "")
        if aid:
            out.append({"id": str(aid), "name": nm})
    return out


def _collect_song_links(body: str, into: dict) -> None:
    """Add /song/<id>/<slug> links from HTML into {id: path}, preserving order."""
    for m in SONG_LINK_RE.finditer(body):
        sid = m.group(1)
        if sid not in into:
            into[sid] = f"/song/{sid}/{m.group(2)}"


def get_artist_overview(artist_id: str):
    """Artist page: name, album links, and any songs shown directly on it."""
    body = _request(f"{BASE}/artist/_/{artist_id}")
    name = ""
    og = OG_TITLE_RE.search(body)
    if og:
        name = unescape(og.group(1)).replace(" - Shazam", "").strip()
    albums = []
    seen_alb = set()
    for m in ALBUM_LINK_RE.finditer(body):
        aid = m.group(1)
        if aid not in seen_alb:
            seen_alb.add(aid)
            albums.append(f"/album/{aid}/{m.group(2)}")
    songs: dict = {}
    _collect_song_links(body, songs)  # singles / tracks shown on the artist page
    return name, albums, songs


def get_album_songs(album_path: str):
    """Album name and its tracks: (album_name, {song_id: path})."""
    body = _request(BASE + album_path)
    album = ""
    og = OG_TITLE_RE.search(body)
    if og:
        raw = unescape(og.group(1))
        album = re.split(r"\s+-\s+album by\b", raw, maxsplit=1)[0].strip()
    songs: dict = {}
    _collect_song_links(body, songs)
    return album, songs


def get_song_count(song_id: str, path: str, album: str) -> Track:
    """Track title and Shazam count from its song page."""
    body = _request(BASE + path)
    count = 0
    m = COUNT_RE.search(body)
    if m:
        count = int(m.group(1).replace(",", ""))
    title = path.rsplit("/", 1)[-1].replace("-", " ").title()  # fallback from slug
    tm = TITLE_RE.search(body)
    if tm:
        raw = unescape(tm.group(1))
        title = re.split(r"\s+[-–]\s+", raw, maxsplit=1)[0].strip() or title
    return Track(song_id=song_id, title=title, album=album, shazam_count=count)


def collect_stats(artist_id: str, artist_name: str, limit: int,
                  workers: int = 8, verbose: bool = True) -> ArtistStats:
    def progress(msg: str) -> None:
        if verbose:
            print(msg, file=sys.stderr, flush=True)

    name, albums, direct_songs = get_artist_overview(artist_id)
    artist_name = artist_name or name or artist_id
    progress(f"Artist \"{artist_name}\": {len(albums)} albums found, collecting tracks...")

    # song_id -> (path, album_name). Albums first so each track is attributed.
    songs: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for album_name, album_songs in pool.map(get_album_songs, albums):
            for sid, path in album_songs.items():
                songs.setdefault(sid, (path, album_name))
    for sid, path in direct_songs.items():  # singles shown on the artist page
        songs.setdefault(sid, (path, ""))

    if not songs:
        raise ShazamError(f"No tracks found for artist {artist_id}.")

    items = [(sid, path, album) for sid, (path, album) in songs.items()][:limit]
    progress(f"{len(items)} tracks. Fetching Shazam counts...")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        tracks = list(pool.map(lambda s: get_song_count(*s), items))

    tracks.sort(key=lambda t: t.shazam_count, reverse=True)
    total = sum(t.shazam_count for t in tracks)
    return ArtistStats(
        artist_id=str(artist_id),
        artist_name=artist_name,
        total_shazams=total,
        track_count=len(tracks),
        tracks=tracks,
    )


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
def render_table(stats: ArtistStats) -> str:
    def grp(n: int) -> str:  # 1234567 -> "1 234 567"
        return f"{n:,}".replace(",", " ")

    lines = [
        f"Artist: {stats.artist_name}  (id {stats.artist_id})",
        f"Total Shazams across {stats.track_count} tracks: {grp(stats.total_shazams)}",
        "",
    ]
    def fit(s: str, w: int) -> str:
        return (s[:w - 1] + "…") if len(s) > w else s

    title_w = min(40, max([len("Track")] + [len(t.title) for t in stats.tracks]))
    album_w = min(34, max([len("Album")] + [len(t.album) for t in stats.tracks]))
    cnt_w = max([len("Shazams")] + [len(grp(t.shazam_count)) for t in stats.tracks])
    header = (f"{'#':>3}  {'Track':<{title_w}}  {'Album':<{album_w}}  "
              f"{'Shazams':>{cnt_w}}")
    lines += [header, "-" * len(header)]
    for i, t in enumerate(stats.tracks, 1):
        lines.append(f"{i:>3}  {fit(t.title, title_w):<{title_w}}  "
                     f"{fit(t.album, album_w):<{album_w}}  "
                     f"{grp(t.shazam_count):>{cnt_w}}")
    return "\n".join(lines)


def render_json(stats: ArtistStats) -> str:
    return json.dumps(asdict(stats), ensure_ascii=False, indent=2)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="shazam-stats",
        description="Shazam statistics for an artist (source: shazam.com).",
    )
    p.add_argument("name", nargs="?", help="artist name to search for")
    p.add_argument("--artist-id", help="exact Shazam artist id (skips name search)")
    p.add_argument("--country", default="us", help="storefront for search (default: us)")
    p.add_argument("--limit", type=int, default=1000,
                   help="max number of tracks to fetch (default: 1000)")
    p.add_argument("--json", action="store_true", help="print JSON instead of a table")
    p.add_argument("-q", "--quiet", action="store_true", help="suppress progress output")
    args = p.parse_args(argv)

    # The Windows console defaults to cp1252; emit UTF-8 so output never breaks.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass

    if not args.artist_id and not args.name:
        p.error("provide an artist name or --artist-id")

    try:
        artist_id = args.artist_id
        artist_name = ""
        if not artist_id:
            matches = search_artist(args.name, args.country)
            if not matches:
                print(f"Artist \"{args.name}\" not found.", file=sys.stderr)
                return 1
            chosen = matches[0]
            artist_id, artist_name = chosen["id"], chosen["name"]
            if len(matches) > 1 and not args.quiet:
                print(f"Selected: {artist_name} (id {artist_id}). Other matches:",
                      file=sys.stderr)
                for m in matches[1:]:
                    print(f"  --artist-id {m['id']}  {m['name']}", file=sys.stderr)
                print("", file=sys.stderr)

        stats = collect_stats(artist_id, artist_name, args.limit, verbose=not args.quiet)
    except ShazamError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(render_json(stats) if args.json else render_table(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
