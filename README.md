# shazam-artist-stats

A tiny, zero-dependency CLI that reports **Shazam counts for an artist** — the
total number of Shazams across their tracks, plus a per-track breakdown sorted
from most to least Shazamed.

```
$ python shazam_stats.py "Personal Animosity"
Artist: PERSONAL ANIMOSITY  (id 1720235535)
Total Shazams across 318 tracks: 8 678

  #  Track                          Album                            Shazams
---------------------------------------------------------------------------
  1  bloody tears (for Julia) [fea… bloody tears (for Julia) [feat…     4 611
  2  ENOUGH                         darkside of love - Single             594
  3  страх                          страх - Single                        389
  ...
```

## Why

Shazam has no official public API for artist statistics. This tool combines two
public sources — no API key, no account, no paid service:

- the **iTunes API** (Apple's catalog, which Shazam shares ids with) to
  enumerate an artist's *complete* discography;
- the public **shazam.com** song pages, which already render each track's
  Shazam count.

## How it works

1. **Resolve the artist** — iTunes search turns a name into an artist id
   (or pass one directly via `--artist-id`).
2. **Walk the full discography** — iTunes lookup returns every release; each
   Shazam album page lists its tracks (and the slugs the song pages need).
3. **Read each track's Shazam count** — every Shazam song page server-renders
   its count next to the Shazam logo icon.
4. **Aggregate** — sum the counts and sort tracks descending.

All HTTP is plain `urllib` from the standard library; album and song pages are
fetched concurrently with a small thread pool. Failed requests are retried with
backoff; a track that still can't be fetched is skipped and listed in the
output (and in `failed` in the JSON), so the total is never silently short.

## Requirements

- Python 3.8+ (uses only the standard library — nothing to `pip install`)

## Tests

```bash
python -m unittest test_shazam_stats -v   # offline, no network access
```

## Usage

```bash
# By artist name (auto-selects the best match; prints other matches to stderr)
python shazam_stats.py "Personal Animosity"

# By exact Apple/Shazam artist id (skips the name search)
python shazam_stats.py --artist-id 1720235535

# Machine-readable output
python shazam_stats.py "Personal Animosity" --json

# Bound the number of tracks fetched (fewer requests, faster)
python shazam_stats.py --artist-id 1720235535 --limit 50

# Quiet mode (no progress on stderr)
python shazam_stats.py "Personal Animosity" -q
```

Ready-made launchers for PERSONAL ANIMOSITY (any extra arguments are passed
through):

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
*Time Rift (Volume 3)* under a separate profile, `1869261655`.

### Options

| Option         | Description                                            |
| -------------- | ------------------------------------------------------ |
| `name`         | Artist name to search for (positional, optional)       |
| `--artist-id`  | Exact Apple/Shazam artist id; skips the name search    |
| `--country`    | iTunes storefront for search & lookup (default: `us`)  |
| `--limit`      | Max number of tracks to fetch (default: `2000`)        |
| `--json`       | Print JSON instead of a table                          |
| `-q`, `--quiet`| Suppress progress and "other matches" messages         |

### JSON shape

```json
{
  "artist_id": "1720235535",
  "artist_name": "PERSONAL ANIMOSITY",
  "total_shazams": 8678,
  "track_count": 318,
  "tracks": [
    {
      "song_id": "1787392151",
      "title": "bloody tears (for Julia) [feat. BLAZER 993]",
      "album": "bloody tears (for Julia) [feat. BLAZER 993] - Single",
      "shazam_count": 4611
    }
  ],
  "failed": []
}
```

`failed` lists the song paths that could not be fetched after retries; those
tracks are not included in `tracks` or `total_shazams`.

## Notes & limitations

- **Unofficial source.** Counts come from the structure of shazam.com song
  pages. If Shazam changes its markup, the tool may need updating. If every
  track comes back as 0, the tool warns that the markup has probably changed.
- **Quirks of shazam.com.** It answers `404` to any request that offers gzip
  (so none is offered), and it sporadically returns `404`/`5xx` for valid
  pages under load (so errors are retried). Tracks with no Shazams have no
  count on their page and are reported as 0.
- **Bot protection.** shazam.com serves a JavaScript "Client Challenge"
  interstitial — with HTTP 200, so it looks like a normal page — on unprefixed
  `/song/<id>/<slug>` paths. Pages are therefore requested under the
  locale-prefixed `/en-US/...` path, and a challenge response is retried and
  then reported as an error rather than parsed as a zero count.
- **Coverage.** The discography is enumerated from the iTunes/Apple catalog
  for the artist id, so it includes albums, EPs, and singles — not just what
  the Shazam artist page happens to show. A track is counted only if it also
  has a Shazam song page.
- **Be polite.** A full catalog means hundreds of requests. Keep the
  concurrency modest and avoid hammering the site; use `--limit` when you don't
  need the full discography.
- For official artist analytics, use
  [Apple Music for Artists](https://artists.apple.com/), which surfaces Shazam
  data for verified artists.

## Roadmap

- **YouTube views and likes** per track, from the official YouTube Data API
  (needs a free API key), configured through a `.env` file. The agreed design
  is recorded in [AGENTS.md](AGENTS.md#planned-youtube-statistics).

## Disclaimer

This project is not affiliated with, endorsed by, or sponsored by Shazam or
Apple. It reads publicly available pages for personal and educational use.
Respect Shazam's Terms of Service and applicable laws when using it.
