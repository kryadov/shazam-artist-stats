# AGENTS.md

Guidance for coding agents working in this repository. User-facing docs live in
[README.md](README.md).

## Project

`shazam_stats.py` is a single-file, **zero-dependency** Python CLI (stdlib only,
Python 3.8+) that reports per-track Shazam counts for an artist:

1. iTunes Search API resolves an artist name to an Apple artist id.
2. iTunes Lookup API lists every release (albums, EPs, singles) of that id.
3. Each Shazam album page (`/en-US/album/<id>/x`) lists its tracks as
   `/song/<id>/<slug>` links.
4. Each Shazam song page server-renders the track's Shazam count.

`personal-animosity.bat` / `.sh` are launchers for artist id `1720235535` that
pass extra arguments through.

## Commands

```bash
python -m unittest test_shazam_stats -v        # offline tests, no network
python shazam_stats.py "Personal Animosity"     # real run (~1 min, ~380 requests)
python shazam_stats.py --artist-id 1720235535 --limit 20   # quick real check
```

Run the offline tests after every change. Do a real run (a small `--limit` is
enough) after touching HTTP code or parsing, because the tests can't catch
changes on shazam.com.

## Code map (`shazam_stats.py`)

| Area | What lives there |
| --- | --- |
| Constants / regexes | `UA`, `SONG_LINK_RE`, `COUNT_RE`, `TITLE_RE`, `CHALLENGE_RE` |
| Data model | `Track`, `ArtistStats` (`failed` = song paths skipped after retries) |
| HTTP | `_fetch` (one request), `_get` (retries + challenge detection), `_itunes` |
| Pipeline | `search_artist`, `get_artist_albums`, `get_album_song_links`, `get_song_count`, `collect_stats` |
| Output | `render_table`, `render_json` |
| CLI | `main` |

Every expected failure raises `ShazamError`. `main` catches it and exits with
code 1. Progress and warnings go to **stderr**, results to **stdout**.

## shazam.com quirks (learned the hard way)

- **Use the locale prefix.** Unprefixed `/song/...` paths get a JS
  "Client Challenge" interstitial **with HTTP 200**. `_get` detects it so it is
  never parsed as a zero-count track.
- **Never send `Accept-Encoding: gzip`.** shazam.com answers `404` to any
  request that offers gzip, even valid pages. curl works because it sends no
  `Accept-Encoding` by default.
- **Sporadic 404/5xx under load.** `_get` retries HTTP and network errors. In
  `collect_stats`, a track that still fails is skipped, listed in
  `ArtistStats.failed` and shown in the output. One bad track must not abort
  the run.
- **Count markup.** The current markup is
  `<use xlink:href="#ShazamLogo"></use></svg><span>2,355,438</span>`. The older
  `tagCountText` span is still accepted. Tracks with no Shazams have **no**
  count element, so a missing match means 0, not an error. If every track
  parses as 0, `collect_stats` warns that the markup has probably changed. Find
  the new form by fetching a popular track (e.g. one from
  `/en-US/charts/top-200/world`) and searching around `ShazamLogo`.
- **Duplicate artist profiles.** One artist can have several Apple artist ids
  with the same name (for PERSONAL ANIMOSITY, `1869261655` holds only
  *Time Rift (Volume 3)*). The name search picks the first match and prints
  the others.

## Conventions

- **Stdlib only.** Don't add third-party dependencies. If a `.env` file is
  needed, parse it in a few lines; don't use `python-dotenv`.
- **Tests are offline.** Mock `_fetch` / pipeline functions with
  `unittest.mock`. Test fixtures copy the *shape* of real pages; hashed CSS
  class suffixes change per build, so never match on them.
- **Don't fail silently.** Bot challenges, markup changes and skipped tracks
  must be visible (error or warning), never a quiet 0.
- **Be polite to shazam.com.** Keep the default concurrency (8 workers) and
  backoff.
- Match the existing style: small functions, `# ---` section banners,
  comments that explain *why*.
- The user often writes in Russian. Answer in the language they use. Code,
  comments, commits and docs stay in English.

## Planned: YouTube statistics

Agreed with the user (2026-10-13) but **not implemented yet**. Implement it
following this design unless the user changes it.

**Goal.** Next to Shazam, show YouTube **views and likes** per track and in
total for the same artist catalog.

**Configuration (`.env` + flags).**
- Commit a `.env.example`; add `.env` to `.gitignore`.
  ```ini
  SOURCES=shazam,youtube   # what to collect: shazam, youtube, or both
  YOUTUBE_API_KEY=
  ```
- Precedence: CLI flag → real environment → `.env` (next to the script) →
  default. The default for `SOURCES` is `shazam`, plus `youtube` if a key is
  set. `--sources` overrides it; `--no-youtube` is shorthand for
  `--sources shazam`.
- If `youtube` is requested explicitly without a key, fail with a clear
  message before any network request.
- If `SOURCES=youtube`, still enumerate the catalog via iTunes but skip the
  shazam.com page fetches.
- Channels are per artist, so they go in flags (and the launchers), not in
  `.env`. `--youtube-topic-channel` overrides the automatic search for
  "`<artist> - Topic`" (one `search.list`, 100 quota units).
  `--youtube-channel` (channel id or `@handle`) adds the official channel.

**Data source.** Use the official YouTube Data API v3 only; no scraping.
1. `channels.list` gets each channel's uploads playlist.
2. `playlistItems.list` lists all uploads, 50 per page.
3. `videos.list` gets `viewCount` / `likeCount`, 50 ids per call.

A full catalog costs about 20 units plus 100 for the Topic search, out of a
10,000/day quota. **Don't use per-track `search.list`**: 100 units × 300+
tracks exceeds the daily quota.

**Risk to check first.** For auto-generated Topic channels, the uploads
playlist can be incomplete or empty. Check this with a real key before
building on it. The fallback is the Topic channel's album playlists
(`playlists.list`).

**Matching (pure functions, `matching.py`).**
- Normalize titles the same way on both sides:
  - NFKC + `casefold` (so Cyrillic titles compare correctly);
  - drop `(feat. …)`, `[feat. …]`, `ft.`, `- Remastered`, `(Official Video)`,
    `(Lyrics)` and `| …` suffixes;
  - for official-channel videos, strip a leading `<Artist> - ` prefix;
  - collapse punctuation and whitespace.
- Match only on **exact** normalized titles. Don't use fuzzy matching: a wrong
  match is worse than a miss, and misses show up as unmatched videos.
- A track can collect several videos (Art Tracks from a single and an album,
  a music video, a lyric video). Sum their views and likes.
- When several catalog rows share a normalized title, the videos go to the
  first row, like Shazam's "first album wins". This keeps totals from being
  double-counted.
- Hidden likes (`likeCount` missing) count as 0, with a note in the summary.

**Data model / output.**
- `Track` gains `youtube_views`, `youtube_likes` and `youtube_videos`
  (list of ids). `None` means "YouTube not collected"; `0` means "collected,
  no video found".
- `ArtistStats` gains `total_youtube_views`, `total_youtube_likes`, the
  resolved channels and `unmatched_videos` (title + views).
- The table shows columns only for enabled sources, with `—` for tracks
  without a video. Add a summary line, e.g.
  `YouTube (<Topic channel>): N views, M likes, 290/318 tracks matched`, and
  list unmatched videos at the end.
- Sort by the first source in `SOURCES`; `--sort shazam|views|likes`
  overrides it.
- JSON carries the same fields. `shazam_count` stays and is `null` in
  youtube-only mode.

**Modules.** `shazam_stats.py` stays the entry point and keeps the Shazam
code. Add:
- `config.py`: `.env` parser, `SOURCES` resolution and validation;
- `youtube.py`: API client, channel/handle resolution, uploads, statistics;
- `matching.py`: normalization and matching.

**Run order.** Validate config and resolve YouTube channels first, so a bad
key or exhausted quota fails fast. Then collect Shazam, then YouTube videos
and stats, then match and render.

**Errors.**
- On API `403` (`keyInvalid`, `quotaExceeded`, `accessNotConfigured`), show a
  clear message with a hint (e.g. "enable YouTube Data API v3"), without
  retrying.
- Retry `5xx` and network errors with backoff.
- If the Topic channel isn't found, warn and suggest `--youtube-topic-channel`.
- If YouTube fails after Shazam succeeded, still print the Shazam results and
  a warning.
- **Strip `key=` from any URL** before it appears in an error or log.

**Tests (offline).**
- Normalization table: feat., Cyrillic, video suffixes, artist prefix.
- Matching: multiple videos per track, duplicate catalog titles, unmatched
  videos.
- `.env` parsing: comments, quotes, precedence; missing-key error.
- Fake API JSON: pagination, missing `likeCount`, `403 quotaExceeded`, key
  redaction.
- Table columns per `SOURCES`, and `--sort`.

Update README (setup of the API key, new options, JSON fields) and the
launchers when this lands.
