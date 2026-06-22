# shazam-artist-stats

A tiny, zero-dependency CLI that reports **Shazam counts for an artist** — the
total number of Shazams across their tracks, plus a per-track breakdown sorted
from most to least Shazamed.

```
$ python shazam_stats.py "Personal Animosity"
Artist: PERSONAL ANIMOSITY  (id 1720235535)
Total Shazams across 140 tracks: 815

  #  Track                          Album                       Shazams
----------------------------------------------------------------------
  1  Arctic Neon Tides              Loprogwave                      161
  2  Frozen Tides Over Neon Shores  Loprogwave                      151
  3  Midnight Assembly Line         Loprogwave                      134
  ...
```

## Why

Shazam has no official public API for artist statistics. This tool reads the
same **public pages** the shazam.com website serves and extracts the numbers
that are already rendered there — no API key, no account, no paid service.

## How it works

1. **Resolve the artist** — by name via Shazam's internal search endpoint
   (using a token the website itself fetches at runtime), or directly via
   `--artist-id`.
2. **Walk the discography** — the artist page lists their albums; each album
   page lists its tracks.
3. **Read each track's Shazam count** — every song page server-renders its
   "tag count" (the number of Shazams).
4. **Aggregate** — sum the counts and sort tracks descending.

All HTTP is plain `urllib` from the standard library; song pages are fetched
concurrently with a small thread pool.

## Requirements

- Python 3.8+ (uses only the standard library — nothing to `pip install`)

## Usage

```bash
# By artist name (auto-selects the best match; prints other matches to stderr)
python shazam_stats.py "Personal Animosity"

# By exact Shazam artist id (skips the name search)
python shazam_stats.py --artist-id 1720235535

# Machine-readable output
python shazam_stats.py "Personal Animosity" --json

# Bound the number of tracks fetched (fewer requests, faster)
python shazam_stats.py --artist-id 1720235535 --limit 50

# Quiet mode (no progress on stderr)
python shazam_stats.py "Personal Animosity" -q
```

### Finding an artist id

It's the last number in a Shazam artist URL:
`https://www.shazam.com/artist/personal-animosity/`**`1720235535`**

### Options

| Option         | Description                                            |
| -------------- | ------------------------------------------------------ |
| `name`         | Artist name to search for (positional, optional)       |
| `--artist-id`  | Exact Shazam artist id; skips the name search          |
| `--country`    | Storefront used for the name search (default: `us`)    |
| `--limit`      | Max number of tracks to fetch (default: `1000`)        |
| `--json`       | Print JSON instead of a table                          |
| `-q`, `--quiet`| Suppress progress and "other matches" messages         |

### JSON shape

```json
{
  "artist_id": "1720235535",
  "artist_name": "PERSONAL ANIMOSITY",
  "total_shazams": 815,
  "track_count": 140,
  "tracks": [
    {
      "song_id": "1884042458",
      "title": "Arctic Neon Tides",
      "album": "Loprogwave",
      "shazam_count": 161
    }
  ]
}
```

## Notes & limitations

- **Unofficial source.** This relies on the structure of shazam.com pages and
  an internal endpoint. If Shazam changes either, the tool may need updating.
- **Coverage.** The total covers tracks reachable from the artist's albums and
  singles as listed on their Shazam page. Tracks that don't appear there are
  not counted.
- **Be polite.** Stats for a large catalog mean many requests. Keep the
  concurrency modest and avoid hammering the site; use `--limit` when you don't
  need the full discography.
- For official artist analytics, use
  [Apple Music for Artists](https://artists.apple.com/), which surfaces Shazam
  data for verified artists.

## Disclaimer

This project is not affiliated with, endorsed by, or sponsored by Shazam or
Apple. It reads publicly available pages for personal and educational use.
Respect Shazam's Terms of Service and applicable laws when using it.
