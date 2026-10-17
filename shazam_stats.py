#!/usr/bin/env python3
"""shazam-artist-stats — Shazam (and YouTube) statistics for a given artist.

Shazam uses Apple's catalog ids, so the discography is enumerated through the
public **iTunes API** (no key required), and the per-track **Shazam counts**
are read from the public song pages on shazam.com:

  1. iTunes search resolves an artist name to an artist id.
  2. iTunes lookup returns the artist's full list of releases (albums/singles).
  3. Each Shazam album page lists its tracks (with the slugs the song pages need).
  4. Each Shazam song page server-renders its Shazam ("tag") count.

Optionally, YouTube views and likes come from the official YouTube Data API
(see youtube.py) and are matched to the catalog by title (see matching.py).

Examples:
    python shazam_stats.py "Personal Animosity"
    python shazam_stats.py --artist-id 1720235535
    python shazam_stats.py "Personal Animosity" --json
    python shazam_stats.py --artist-id 1720235535 --limit 200
    python shazam_stats.py "Personal Animosity" --sources youtube --sort likes
"""

import argparse
import gzip
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, asdict, field
from html import unescape
from typing import Optional

import youtube
from config import ConfigError, load_config
from matching import match_videos

BASE = "https://www.shazam.com"
# Unprefixed /song/<id>/<slug> paths are served a bot interstitial (HTTP 200 with a
# "Client Challenge" body); the locale-prefixed variants return the real pages.
# The prefix is case-sensitive: /en-US works, /en-us is challenged.
LOCALE = "en-US"
ITUNES = "https://itunes.apple.com/{op}"

UA = ("Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) "
      "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1")

# /song/<id>/<slug> links found on Shazam album pages.
SONG_LINK_RE = re.compile(r'/song/(\d+)/([a-z0-9][a-z0-9-]*)')
# Shazam count on a song page, next to the Shazam logo icon:
#   <use xlink:href="#ShazamLogo"></use></svg><span>2,355,438</span>
# Older builds used <span class="...tagCountText...">123</span>; still accepted.
COUNT_RE = re.compile(r'#ShazamLogo"></use></svg><span>\s*([\d,]+)\s*<'
                      r'|tagCountText[^"]*">\s*([\d,]+)\s*<')
TITLE_RE = re.compile(r'<title>([^<]+)</title>', re.I)
# The bot interstitial: a tiny JS-only page titled "Client Challenge", served with 200.
CHALLENGE_RE = re.compile(r'<title>\s*Client Challenge\s*</title>', re.I)


class ShazamError(Exception):
    pass


@dataclass
class Track:
    song_id: str
    title: str
    album: str
    # None: the source was not collected. 0 for YouTube: no matching video.
    shazam_count: Optional[int] = None
    youtube_views: Optional[int] = None
    youtube_likes: Optional[int] = None
    youtube_videos: Optional[list] = None  # matched video ids


@dataclass
class ArtistStats:
    artist_id: str
    artist_name: str
    total_shazams: Optional[int]
    track_count: int
    tracks: list  # list[Track]
    failed: list = field(default_factory=list)  # song paths that could not be fetched
    sources: list = field(default_factory=lambda: ["shazam"])
    total_youtube_views: Optional[int] = None  # matched videos only
    total_youtube_likes: Optional[int] = None
    youtube_tracks_matched: Optional[int] = None
    youtube_hidden_likes: Optional[int] = None  # matched videos counted as 0 likes
    youtube_channels: list = field(default_factory=list)  # [{id, title, official}]
    unmatched_videos: list = field(default_factory=list)  # [{id, title, views}]


# --------------------------------------------------------------------------- #
# HTTP
# --------------------------------------------------------------------------- #
def song_url(path: str) -> str:
    """Absolute URL for a /song/<id>/<slug> path scraped off an album page."""
    return f"{BASE}/{LOCALE}{path}"


def album_url(album_id: str) -> str:
    return f"{BASE}/{LOCALE}/album/{album_id}/x"


def is_challenge(body: str) -> bool:
    return CHALLENGE_RE.search(body) is not None


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        # No Accept-Encoding: shazam.com answers 404 to any request offering gzip.
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


def _get(url: str, retries: int = 3, backoff: float = 1.5) -> str:
    """Fetch a page, rejecting the bot interstitial instead of parsing it as data.

    The interstitial comes back with HTTP 200, so without this check a blocked
    request silently looks like a track with zero Shazams.

    Shazam also returns sporadic 404s/5xx for valid pages under load, so HTTP and
    network errors are retried too.
    """
    for attempt in range(1, retries + 1):
        try:
            body = _fetch(url)
        except ShazamError:
            if attempt == retries:
                raise
        else:
            if not is_challenge(body):
                return body
        if attempt < retries:
            time.sleep(backoff * attempt)
    raise ShazamError(f"Blocked by bot challenge after {retries} attempts: {url}")


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


def get_itunes_tracks(albums: list, country: str, batch: int = 20):
    """Catalog tracks straight from iTunes: [(song_id, title, album_name), ...].

    Used when Shazam is not collected, so no shazam.com page is fetched. iTunes
    track ids are the same Apple ids that Shazam song pages use.
    """
    names = dict(albums)
    tracks: dict = {}  # song_id -> (title, album); first album wins, as for Shazam
    by_album: dict = {cid: [] for cid, _ in albums}
    for start in range(0, len(albums), batch):
        ids = [cid for cid, _ in albums[start:start + batch]]
        data = _itunes("lookup", {"id": ",".join(ids), "entity": "song",
                                  "limit": 200, "country": country})
        for r in data.get("results", []):
            cid = str(r.get("collectionId", ""))
            if r.get("wrapperType") == "track" and r.get("trackId") and cid in by_album:
                by_album[cid].append(r)
    for cid, _ in albums:
        for r in sorted(by_album[cid], key=lambda r: (r.get("discNumber", 1),
                                                      r.get("trackNumber", 0))):
            tracks.setdefault(str(r["trackId"]), (r.get("trackName", ""), names[cid]))
    return [(sid, title, album) for sid, (title, album) in tracks.items()]


def get_album_song_links(album_id: str):
    """Tracks on a Shazam album page: [(song_id, path), ...] (path carries the slug)."""
    body = _get(album_url(album_id))
    songs: dict = {}
    for m in SONG_LINK_RE.finditer(body):
        sid = m.group(1)
        if sid not in songs:
            songs[sid] = f"/song/{sid}/{m.group(2)}"
    return list(songs.items())


def get_song_count(song_id: str, path: str, album: str, **kw) -> Track:
    """Track title and Shazam count from its song page."""
    body = _get(song_url(path), **kw)
    count = 0
    m = COUNT_RE.search(body)
    if m:
        count = int((m.group(1) or m.group(2)).replace(",", ""))
    title = path.rsplit("/", 1)[-1].replace("-", " ").title()  # fallback from slug
    tm = TITLE_RE.search(body)
    if tm:
        raw = unescape(tm.group(1))
        title = re.split(r"\s+[-–]\s+", raw, maxsplit=1)[0].strip() or title
    return Track(song_id=song_id, title=title, album=album, shazam_count=count)


def _progress(verbose: bool):
    def progress(msg: str) -> None:
        if verbose:
            print(msg, file=sys.stderr, flush=True)
    return progress


def collect_stats(artist_id: str, artist_name: str, country: str, limit: int,
                  workers: int = 8, verbose: bool = True, shazam: bool = True,
                  catalog: Optional[tuple] = None) -> ArtistStats:
    """Catalog tracks (in catalog order) with Shazam counts if `shazam`.

    `catalog` is a prefetched get_artist_albums() result.
    """
    progress = _progress(verbose)
    name, albums = catalog if catalog is not None else get_artist_albums(artist_id, country)
    artist_name = artist_name or name or artist_id
    if not albums:
        raise ShazamError(f"No releases found for artist {artist_id}.")
    progress(f"Artist \"{artist_name}\": {len(albums)} releases found, collecting tracks...")

    if not shazam:
        items = get_itunes_tracks(albums, country)[:limit]
        if not items:
            raise ShazamError(f"No tracks found for artist {artist_id}.")
        progress(f"{len(items)} tracks (Shazam not collected).")
        tracks = [Track(song_id=sid, title=title, album=album)
                  for sid, title, album in items]
        return ArtistStats(artist_id=str(artist_id), artist_name=artist_name,
                           total_shazams=None, track_count=len(tracks),
                           tracks=tracks, sources=[])

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

    def fetch(item):
        try:
            return get_song_count(*item)
        except ShazamError as e:
            progress(f"Warning: skipping track: {e}")
            return None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(fetch, items))
    tracks = [t for t in results if t is not None]
    failed = [path for (_, path, _), t in zip(items, results) if t is None]
    if not tracks:
        raise ShazamError(f"Could not fetch any of {len(items)} tracks.")

    total = sum(t.shazam_count for t in tracks)
    if total == 0 and len(tracks) > 1:
        progress("Warning: every track parsed as 0 Shazams; the song-page markup "
                 "may have changed (see COUNT_RE).")
    return ArtistStats(
        artist_id=str(artist_id),
        artist_name=artist_name,
        total_shazams=total,
        track_count=len(tracks),
        tracks=tracks,
        failed=failed,
    )


# --------------------------------------------------------------------------- #
# YouTube
# --------------------------------------------------------------------------- #
def resolve_youtube_channels(client: "youtube.Client", artist_name: str,
                             topic_refs: list, official_refs: list,
                             verbose: bool = True) -> list:
    """Topic channel(s) (searched by name unless given) plus official channels."""
    progress = _progress(verbose)
    topic_ids = topic_refs or client.find_topic_channels(artist_name)
    if not topic_ids:
        print(f"Warning: no YouTube channel titled \"{artist_name} - Topic\" was "
              f"found; pass it with --youtube-topic-channel <id or @handle>.",
              file=sys.stderr)
    channels = client.get_channels(topic_ids) if topic_ids else []
    if official_refs:
        channels += client.get_channels(official_refs, official=True)
    for ch in channels:
        kind = "official" if ch.official else "topic"
        progress(f"YouTube channel ({kind}): {ch.title} ({ch.id})")
    return channels


def add_youtube(stats: ArtistStats, client: "youtube.Client", channels: list,
                verbose: bool = True, titles: Optional[dict] = None) -> None:
    """Fetch every upload of `channels` and attach matched views/likes to tracks.

    `titles` maps song_id -> iTunes track name. Shazam page titles drop version
    suffixes ("NN (MIXED)" reads "NN"), so the iTunes names replace them before
    matching. Must run while `stats.tracks` is still in catalog order: when
    several rows share a title, the first one gets the videos.
    """
    progress = _progress(verbose)
    for t in stats.tracks:
        t.title = (titles or {}).get(t.song_id) or t.title
    progress("Fetching YouTube uploads and statistics...")
    videos = client.collect_videos(channels)
    per_row, unmatched = match_videos([t.title for t in stats.tracks], videos,
                                      stats.artist_name)
    for t, vids in zip(stats.tracks, per_row):
        t.youtube_views = sum(v.views for v in vids)
        t.youtube_likes = sum(v.likes or 0 for v in vids)  # hidden likes count as 0
        t.youtube_videos = [v.id for v in vids]
    matched = [v for vids in per_row for v in vids]
    stats.total_youtube_views = sum(v.views for v in matched)
    stats.total_youtube_likes = sum(v.likes or 0 for v in matched)
    stats.youtube_tracks_matched = sum(1 for vids in per_row if vids)
    stats.youtube_hidden_likes = sum(1 for v in matched if v.likes is None)
    stats.youtube_channels = [{"id": c.id, "title": c.title, "official": c.official}
                              for c in channels]
    stats.unmatched_videos = [{"id": v.id, "title": v.title, "views": v.views}
                              for v in sorted(unmatched, key=lambda v: -v.views)]
    progress(f"YouTube: {len(videos)} videos, {len(matched)} matched "
             f"({client.units} quota units used).")


SORT_KEYS = {
    "shazam": lambda t: t.shazam_count,
    "views": lambda t: t.youtube_views,
    "likes": lambda t: t.youtube_likes,
}


def sort_tracks(stats: ArtistStats, key: str) -> None:
    """Sort descending; ties keep catalog order (the sort is stable)."""
    get = SORT_KEYS[key]
    stats.tracks.sort(key=lambda t: get(t) if get(t) is not None else -1, reverse=True)


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #
UNMATCHED_SHOWN = 30


def render_table(stats: ArtistStats) -> str:
    def grp(n: int) -> str:  # 1234567 -> "1 234 567"
        return f"{n:,}".replace(",", " ")

    def fit(s: str, w: int) -> str:
        return (s[:w - 1] + "…") if len(s) > w else s

    def yt_cell(t: Track) -> str:  # "—": no matching video
        if not t.youtube_videos:
            return "—"
        return f"{grp(t.youtube_views)} / {grp(t.youtube_likes)}"

    shazam = "shazam" in stats.sources
    yt = "youtube" in stats.sources
    lines = [f"Artist: {stats.artist_name}  (id {stats.artist_id})"]
    if shazam:
        lines.append(f"Total Shazams across {stats.track_count} tracks: "
                     f"{grp(stats.total_shazams)}")
    else:
        lines.append(f"Tracks: {stats.track_count}")
    if yt:
        names = ", ".join(dict.fromkeys(c["title"] for c in stats.youtube_channels))
        lines.append(f"YouTube ({names}): {grp(stats.total_youtube_views)} views, "
                     f"{grp(stats.total_youtube_likes)} likes, "
                     f"{stats.youtube_tracks_matched}/{stats.track_count} tracks matched")
        if stats.youtube_hidden_likes:
            lines.append(f"Note: {stats.youtube_hidden_likes} matched video(s) hide "
                         f"their likes; counted as 0.")
    if stats.failed:
        lines.append(f"Warning: {len(stats.failed)} track(s) could not be fetched "
                     f"and are not counted: {', '.join(stats.failed)}")
    lines.append("")

    # (header, cell function) for each enabled source, in table order.
    columns = []
    if shazam:
        columns.append(("Shazams", lambda t: grp(t.shazam_count)))
    if yt:
        columns.append(("YouTube (views / likes)", yt_cell))
    title_w = min(40, max([len("Track")] + [len(t.title) for t in stats.tracks]))
    album_w = min(34, max([len("Album")] + [len(t.album) for t in stats.tracks]))
    widths = [max([len(h)] + [len(cell(t)) for t in stats.tracks]) for h, cell in columns]
    header = (f"{'#':>3}  {'Track':<{title_w}}  {'Album':<{album_w}}"
              + "".join(f"  {h:>{w}}" for (h, _), w in zip(columns, widths)))
    lines += [header, "-" * len(header)]
    for i, t in enumerate(stats.tracks, 1):
        lines.append(f"{i:>3}  {fit(t.title, title_w):<{title_w}}  "
                     f"{fit(t.album, album_w):<{album_w}}"
                     + "".join(f"  {cell(t):>{w}}" for (_, cell), w in zip(columns, widths)))

    if yt and stats.unmatched_videos:
        views = sum(v["views"] for v in stats.unmatched_videos)
        lines += ["", f"Unmatched YouTube videos ({len(stats.unmatched_videos)}, "
                      f"{grp(views)} views, not counted above):"]
        # With --limit most uploads are unmatched; keep the table readable.
        shown = stats.unmatched_videos[:UNMATCHED_SHOWN]
        view_w = max(len(grp(v["views"])) for v in shown)
        for v in shown:
            lines.append(f"  {grp(v['views']):>{view_w}}  {v['title']}  ({v['id']})")
        rest = len(stats.unmatched_videos) - len(shown)
        if rest:
            lines.append(f"  ... and {rest} more (full list in --json)")
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
    src = p.add_mutually_exclusive_group()
    src.add_argument("--sources",
                     help="what to collect: shazam, youtube or shazam,youtube "
                          "(default: SOURCES from .env, else shazam + youtube "
                          "if YOUTUBE_API_KEY is set)")
    src.add_argument("--no-youtube", action="store_true",
                     help="shorthand for --sources shazam")
    p.add_argument("--sort", choices=sorted(SORT_KEYS),
                   help="sort column (default: the first source)")
    p.add_argument("--youtube-topic-channel", action="append", default=[],
                   metavar="ID_OR_HANDLE",
                   help="the artist's \"<artist> - Topic\" channel; skips the "
                        "search (saves 100 quota units). Repeatable")
    p.add_argument("--youtube-channel", action="append", default=[],
                   metavar="ID_OR_HANDLE",
                   help="the artist's official channel (id or @handle). Repeatable")
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
        cfg = load_config(args.sources, args.no_youtube)
    except ConfigError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    sources = cfg.sources
    if args.sort in ("views", "likes") and "youtube" not in sources:
        p.error(f"--sort {args.sort} needs the youtube source")
    if args.sort == "shazam" and "shazam" not in sources:
        p.error("--sort shazam needs the shazam source")

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

        # The catalog gives the artist name the Topic channel search needs.
        catalog = get_artist_albums(artist_id, args.country)
        artist_name = artist_name or catalog[0] or artist_id

        # Resolve YouTube channels before the slow Shazam part, so a bad key or
        # an exhausted quota fails fast.
        channels, client = [], None
        if "youtube" in sources:
            client = youtube.Client(cfg.youtube_api_key)
            channels = resolve_youtube_channels(
                client, artist_name, args.youtube_topic_channel,
                args.youtube_channel, verbose=not args.quiet)
            if not channels:
                if sources == ["youtube"]:
                    raise youtube.YouTubeError("No YouTube channel to collect from.")
                sources = [s for s in sources if s != "youtube"]

        stats = collect_stats(artist_id, artist_name, args.country, args.limit,
                              verbose=not args.quiet, shazam="shazam" in sources,
                              catalog=catalog)

        if channels:
            titles = None
            if "shazam" in sources:
                # Canonical names for matching (same Apple song ids as Shazam).
                titles = {sid: title for sid, title, _ in
                          get_itunes_tracks(catalog[1], args.country)}
            try:
                add_youtube(stats, client, channels, verbose=not args.quiet,
                            titles=titles)
            except youtube.YouTubeError as e:
                if "shazam" not in sources:
                    raise
                # Don't throw away a finished Shazam run over a YouTube failure.
                print(f"Warning: YouTube statistics skipped: {e}", file=sys.stderr)
                sources = [s for s in sources if s != "youtube"]
    except (ShazamError, youtube.YouTubeError) as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    stats.sources = sources
    sort = args.sort
    if sort in ("views", "likes") and "youtube" not in sources:
        sort = None  # YouTube was dropped at runtime; fall back to Shazam
    sort_tracks(stats, sort or ("shazam" if sources[0] == "shazam" else "views"))
    print(render_json(stats) if args.json else render_table(stats))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
