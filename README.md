# shazam-artist-stats

A tiny, zero-dependency CLI that reports **Shazam counts** and, optionally,
**YouTube views and likes** for an artist: totals across the whole catalog plus
a per-track breakdown.

```
$ python shazam_stats.py "Personal Animosity"
Artist: PERSONAL ANIMOSITY  (id 1720235535)
Total Shazams across 319 tracks: 8 724
YouTube (PERSONAL ANIMOSITY - Topic): 79 711 views, 925 likes, 305/319 tracks matched

  #  Track                          Album                            Shazams  YouTube (views / likes)
-----------------------------------------------------------------------------------------------------
  1  bloody tears (for Julia) [fea… bloody tears (for Julia) [feat…    4 612             31 206 / 284
  2  ENOUGH                         darkside of love - Single            595               1 083 / 24
  3  страх                          страх - Single                       389               3 356 / 34
  ...
 16  Cupid                          Cupid - Single                        34                        —
  ...

Unmatched YouTube videos (15, 861 views, not counted above):
  427  Neon Night  (nTMYqlNmj5c)
  ...
```

`—` means no YouTube video was found for the track.

## Why

Shazam has no official public API for artist statistics. This tool combines
public sources:

- the **iTunes API** (Apple's catalog, which Shazam shares ids with) to
  enumerate an artist's *complete* discography — no key needed;
- the public **shazam.com** song pages, which already render each track's
  Shazam count — no key needed;
- the official **YouTube Data API v3** for views and likes — needs a free API
  key; optional.

## How it works

1. **Resolve the artist** — iTunes search turns a name into an artist id
   (or pass one directly via `--artist-id`).
2. **Walk the full discography** — iTunes lookup returns every release; each
   Shazam album page lists its tracks (and the slugs the song pages need).
3. **Read each track's Shazam count** — every Shazam song page server-renders
   its count next to the Shazam logo icon.
4. **YouTube (optional)** — find the artist's auto-generated
   "`<artist> - Topic`" channel, list all its uploads, fetch views and likes,
   and match the videos to catalog tracks by title (see
   [Matching](#how-youtube-videos-are-matched)).
5. **Aggregate** — sum the counts and sort tracks descending.

All HTTP is plain `urllib` from the standard library; album and song pages are
fetched concurrently with a small thread pool. Failed requests are retried with
backoff; a track that still can't be fetched is skipped and listed in the
output (and in `failed` in the JSON), so the total is never silently short.

## Requirements

- Python 3.8+ (uses only the standard library — nothing to `pip install`)
- For YouTube: a YouTube Data API v3 key (free)

## Setup for YouTube

1. In the [Google Cloud console](https://console.cloud.google.com/), create a
   project, enable **YouTube Data API v3**
   (*APIs & Services → Library*), and create an **API key**
   (*APIs & Services → Credentials*).
2. Copy `.env.example` to `.env` (next to `shazam_stats.py`) and set the key:

   ```ini
   SOURCES=shazam,youtube   # what to collect: shazam, youtube, or both
   YOUTUBE_API_KEY=AIza...
   ```

`.env` is git-ignored. Settings are read in this order: CLI flag → real
environment variable → `.env` → default. Without `SOURCES`, the tool collects
Shazam, plus YouTube when a key is set. To turn YouTube off, set
`SOURCES=shazam` or pass `--no-youtube`.

**Quota.** A full catalog run costs about 20 units, plus 100 for the Topic
channel search, out of the default 10,000 units per day. Pass
`--youtube-topic-channel` to skip the search.

## Tests

```bash
python -m unittest -v   # offline, no network access
```

## Usage

```bash
# By artist name (auto-selects the best match; prints other matches to stderr)
python shazam_stats.py "Personal Animosity"

# By exact Apple/Shazam artist id (skips the name search)
python shazam_stats.py --artist-id 1720235535

# Machine-readable output
python shazam_stats.py "Personal Animosity" --json

# Only YouTube (no shazam.com requests), sorted by likes
python shazam_stats.py "Personal Animosity" --sources youtube --sort likes

# Only Shazam, even if a YouTube key is configured
python shazam_stats.py "Personal Animosity" --no-youtube

# Pin the Topic channel and add the official channel
python shazam_stats.py --artist-id 1720235535 \
    --youtube-topic-channel UCW0XqjvAVf_zimSmZ9MzTaA --youtube-channel @somehandle

# Bound the number of tracks fetched (fewer requests, faster)
python shazam_stats.py --artist-id 1720235535 --limit 50

# Quiet mode (no progress on stderr)
python shazam_stats.py "Personal Animosity" -q
```

Ready-made launchers for PERSONAL ANIMOSITY (any extra arguments are passed
through). They also pin both of the artist's Topic channels:

```bash
./personal-animosity.sh            # Linux/macOS/Git Bash
personal-animosity.bat --json      # Windows (sets UTF-8 output for the console)
```

### Finding an artist id

It's the last number in a Shazam artist URL (the same id Apple uses):
`https://www.shazam.com/artist/personal-animosity/`**`1720235535`**

One artist can have **several Apple artist profiles** with the same name — for
example when a distributor files a release under a duplicate profile. A name
search picks the first match and prints the others to stderr; run each with
`--artist-id` and add the totals. PERSONAL ANIMOSITY, for instance, has
*Time Rift (Volume 3)* under a separate profile, `1869261655`. Its tracks
are uploaded to the same Topic channel, so they show up as unmatched videos
when you run the main profile.

### Options

| Option                      | Description |
| --------------------------- | ----------- |
| `name`                      | Artist name to search for (positional, optional) |
| `--artist-id`               | Exact Apple/Shazam artist id; skips the name search |
| `--country`                 | iTunes storefront for search & lookup (default: `us`) |
| `--limit`                   | Max number of tracks to fetch (default: `2000`) |
| `--json`                    | Print JSON instead of a table |
| `-q`, `--quiet`             | Suppress progress and "other matches" messages |
| `--sources`                 | `shazam`, `youtube` or `shazam,youtube`; overrides `SOURCES` |
| `--no-youtube`              | Shorthand for `--sources shazam` |
| `--sort`                    | `shazam`, `views` or `likes` (default: the first source) |
| `--youtube-topic-channel`   | Topic channel id or `@handle`; skips the search. Repeatable |
| `--youtube-channel`         | Official channel id or `@handle` to include too. Repeatable |

### JSON shape

```json
{
  "artist_id": "1720235535",
  "artist_name": "PERSONAL ANIMOSITY",
  "total_shazams": 8724,
  "track_count": 319,
  "tracks": [
    {
      "song_id": "1787392151",
      "title": "bloody tears (for Julia) [feat. BLAZER 993]",
      "album": "bloody tears (for Julia) [feat. BLAZER 993] - Single",
      "shazam_count": 4612,
      "youtube_views": 31206,
      "youtube_likes": 284,
      "youtube_videos": ["..."]
    }
  ],
  "failed": [],
  "sources": ["shazam", "youtube"],
  "total_youtube_views": 79711,
  "total_youtube_likes": 925,
  "youtube_tracks_matched": 305,
  "youtube_hidden_likes": 0,
  "youtube_channels": [
    {"id": "UCW0XqjvAVf_zimSmZ9MzTaA", "title": "PERSONAL ANIMOSITY - Topic", "official": false}
  ],
  "unmatched_videos": [{"id": "nTMYqlNmj5c", "title": "Neon Night", "views": 427}]
}
```

- `failed` lists the song paths that could not be fetched after retries; those
  tracks are not included in `tracks` or `total_shazams`.
- A field is `null` when its source wasn't collected: `shazam_count` and
  `total_shazams` with `--sources youtube`, the `youtube_*` fields without
  YouTube. `youtube_views: 0` with empty `youtube_videos` means "collected, no
  video found".
- YouTube totals count matched videos only; `unmatched_videos` holds the rest.
- `youtube_hidden_likes` counts matched videos whose uploader hides likes;
  those count as 0 likes.

### How YouTube videos are matched

- The **Topic channel** is found by searching for a channel titled exactly
  "`<artist> - Topic`". If several have that title (PERSONAL ANIMOSITY has
  two), all are used. If none is found, the tool warns and suggests
  `--youtube-topic-channel`.
- Titles are normalized on both sides: Unicode NFKC + case folding (works for
  Cyrillic), `(feat. …)` / `ft. …`, `(Official Video)`, `(Lyrics)`,
  `- Remastered` and `| …` are dropped, and punctuation is collapsed. For
  official-channel videos, a leading "`<Artist> - `" is stripped too.
- Only **exact** normalized titles match — no fuzzy matching, because a wrong
  match is worse than a miss. Misses stay visible in the unmatched list.
- A track can collect several videos (e.g. Art Tracks from both a single and an
  album, or a music video); their views and likes are summed. When several
  catalog rows share a title, the videos go to the first one, so nothing is
  counted twice.
- When YouTube is collected, track titles come from iTunes rather than from
  the Shazam page, because Shazam drops version suffixes (it shows
  "NN (MIXED)" as "NN").

## Notes & limitations

- **Unofficial Shazam source.** Counts come from the structure of shazam.com
  song pages. If Shazam changes its markup, the tool may need updating. If
  every track comes back as 0, the tool warns that the markup has probably
  changed.
- **Quirks of shazam.com.** It answers `404` to any request that offers gzip
  (so none is offered), and it sporadically returns `404`/`5xx` for valid
  pages under load (so errors are retried). Tracks with no Shazams have no
  count on their page and are reported as 0.
- **Bot protection.** shazam.com serves a JavaScript "Client Challenge"
  interstitial — with HTTP 200, so it looks like a normal page — on unprefixed
  `/song/<id>/<slug>` paths. Pages are therefore requested under the
  locale-prefixed `/en-US/...` path, and a challenge response is retried and
  then reported as an error rather than parsed as a zero count.
- **YouTube errors.** An invalid key, an exhausted quota or a disabled API
  stops the run before the slow Shazam part, with a hint. If YouTube fails
  later, the Shazam results are still printed, with a warning. The API key is
  never shown in error messages.
- **Coverage.** The discography is enumerated from the iTunes/Apple catalog
  for the artist id, so it includes albums, EPs, and singles — not just what
  the Shazam artist page happens to show. A track is counted only if it also
  has a Shazam song page.
- **Be polite.** A full catalog means hundreds of requests. Keep the
  concurrency modest and avoid hammering the site; use `--limit` when you don't
  need the full discography.
- For official artist analytics, use
  [Apple Music for Artists](https://artists.apple.com/), which surfaces Shazam
  data for verified artists, and YouTube Studio.

## Disclaimer

This project is not affiliated with, endorsed by, or sponsored by Shazam,
Apple, or YouTube. It reads publicly available pages and official APIs for
personal and educational use. Respect the services' Terms of Service and
applicable laws when using it.
