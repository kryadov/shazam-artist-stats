#!/usr/bin/env python3
"""shazam-artist-stats — Shazam statistics for a given artist.

Shazam uses Apple's catalog ids, so the discography is enumerated through the
public **iTunes API** (no key required), and the per-track **Shazam counts**
are read from the public song pages on shazam.com:

  1. iTunes search resolves an artist name to an artist id.
  2. iTunes lookup returns the artist's full list of releases (albums/singles).
  3. Each Shazam album page lists its tracks (with the slugs the song pages need).
  4. Each Shazam song page server-renders its Shazam ("tag") count.

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
ITUNES = "https://itunes.apple.com/{op}"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

# /song/<id>/<slug> links found on Shazam album pages.
SONG_LINK_RE = re.compile(r'/song/(\d+)/([a-z0-9][a-z0-9-]*)')
# Shazam count on a song page: <span class="...tagCountText...">123</span>
COUNT_RE = re.compile(r'tagCountText[^"]*">\s*([\d,]+)\s*<')
TITLE_RE = re.compile(r'<title>([^<]+)</title>', re.I)


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
def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, identity",
    })
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


def _itunes(op: str, params: dict) -> dict:
    url = ITUNES.format(op=op) + "?" + urllib.parse.urlencode(params)
    body = _get(url)
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ShazamError(f"Could not parse iTunes response: {e}") from e


# --------------------------------------------------------------------------- #
# Pipeline steps
# --------------------------------------------------------------------------- #
def search_artist(name: str, country: str):
    """Resolve an artist name to matches, ordered by relevance."""
    data = _itunes("search", {"term": name, "entity": "musicArtist",
                              "limit": 10, "country": country})
    out = []
    for r in data.get("results", []):
        aid = r.get("artistId")
        if aid:
            out.append({"id": str(aid), "name": r.get("artistName", "")})
    return out


def get_artist_albums(artist_id: str, country: str):
    """Artist name and the full list of releases: (name, [(album_id, album_name), ...])."""
    data = _itunes("lookup", {"id": artist_id, "entity": "album",
                              "limit": 200, "country": country})
    name = ""
    albums = []
    for r in data.get("results", []):
        if r.get("wrapperType") == "artist":
            name = r.get("artistName", "") or name
        elif r.get("wrapperType") == "collection":
            cid = r.get("collectionId")
            if cid:
                albums.append((str(cid), r.get("collectionName", "")))
    return name, albums


def get_album_song_links(album_id: str):
    """Tracks on a Shazam album page: [(song_id, path), ...] (path carries the slug)."""
    body = _get(f"{BASE}/album/{album_id}/x")
    songs: dict = {}
    for m in SONG_LINK_RE.finditer(body):
        sid = m.group(1)
        if sid not in songs:
            songs[sid] = f"/song/{sid}/{m.group(2)}"
    return list(songs.items())


def get_song_count(song_id: str, path: str, album: str) -> Track:
    """Track title and Shazam count from its song page."""
    body = _get(BASE + path)
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


def collect_stats(artist_id: str, artist_name: str, country: str, limit: int,
                  workers: int = 8, verbose: bool = True) -> ArtistStats:
    def progress(msg: str) -> None:
        if verbose:
            print(msg, file=sys.stderr, flush=True)

    name, albums = get_artist_albums(artist_id, country)
    artist_name = artist_name or name or artist_id
    if not albums:
        raise ShazamError(f"No releases found for artist {artist_id}.")
    progress(f"Artist \"{artist_name}\": {len(albums)} releases found, collecting tracks...")

    # song_id -> (path, album_name); first album that lists a track wins.
    songs: dict = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        link_lists = pool.map(get_album_song_links, [cid for cid, _ in albums])
        for (cid, album_name), links in zip(albums, link_lists):
            for sid, path in links:
                songs.setdefault(sid, (path, album_name))

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

    def fit(s: str, w: int) -> str:
        return (s[:w - 1] + "…") if len(s) > w else s

    lines = [
        f"Artist: {stats.artist_name}  (id {stats.artist_id})",
        f"Total Shazams across {stats.track_count} tracks: {grp(stats.total_shazams)}",
        "",
    ]
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
        description="Shazam statistics for an artist (iTunes catalog + shazam.com).",
    )
    p.add_argument("name", nargs="?", help="artist name to search for")
    p.add_argument("--artist-id", help="exact Apple/Shazam artist id (skips name search)")
    p.add_argument("--country", default="us", help="iTunes storefront (default: us)")
    p.add_argument("--limit", type=int, default=2000,
                   help="max number of tracks to fetch (default: 2000)")
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

        stats = collect_stats(artist_id, artist_name, args.country, args.limit,
                              verbose=not args.quiet)
    except ShazamError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(render_json(stats) if args.json else render_table(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
