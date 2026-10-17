# AGENTS.md

Guidance for coding agents working in this repository. User-facing docs live in
[README.md](README.md).

## Project

A **zero-dependency** Python CLI (stdlib only, Python 3.8+) that reports
per-track Shazam counts and, optionally, YouTube views/likes for an artist:

1. iTunes Search API resolves an artist name to an Apple artist id.
2. iTunes Lookup API lists every release (albums, EPs, singles) of that id.
3. Each Shazam album page (`/en-US/album/<id>/x`) lists its tracks as
   `/song/<id>/<slug>` links.
4. Each Shazam song page server-renders the track's Shazam count.
5. YouTube (optional): the official Data API v3 lists every upload of the
   artist's "`<artist> - Topic`" channel(s) with views/likes; videos are
   matched to catalog tracks by normalized title.

The input stays an artist name or `--artist-id`; everything else is derived.
`personal-animosity.bat` / `.sh` are launchers for artist id `1720235535` that
pin its two Topic channels and pass extra arguments through.

## Commands

```bash
python -m unittest -v                           # all offline tests, no network
python shazam_stats.py "Personal Animosity"     # real run (~1 min, ~380 requests)
python shazam_stats.py --artist-id 1720235535 --limit 20   # quick real check
python shazam_stats.py --artist-id 1720235535 --sources youtube  # YouTube only
```

Run the offline tests after every change. Do a real run (a small `--limit` is
enough) after touching HTTP code or parsing, because the tests can't catch
changes on shazam.com or in the API. YouTube runs need `YOUTUBE_API_KEY` in
`.env`; never print or commit it.

## Code map

| File / area | What lives there |
| --- | --- |
| `shazam_stats.py` constants | `UA`, `SONG_LINK_RE`, `COUNT_RE`, `TITLE_RE`, `CHALLENGE_RE` |
| `shazam_stats.py` data model | `Track`, `ArtistStats` (`failed` = song paths skipped after retries; `None` = source not collected) |
| `shazam_stats.py` HTTP | `_fetch` (one request), `_get` (retries + challenge detection), `_itunes` |
| `shazam_stats.py` pipeline | `search_artist`, `get_artist_albums`, `get_itunes_tracks`, `get_album_song_links`, `get_song_count`, `collect_stats` |
| `shazam_stats.py` YouTube glue | `resolve_youtube_channels`, `add_youtube`, `sort_tracks` |
| `shazam_stats.py` output / CLI | `render_table`, `render_json`, `main` |
| `config.py` | `.env` parser, `SOURCES` / `YOUTUBE_API_KEY` resolution (`load_config`) |
| `youtube.py` | `Client` (API calls, retries, quota counter), `redact`, `ApiError` hints |
| `matching.py` | `normalize`, `match_videos` (pure functions) |

Expected failures raise `ShazamError`, `ConfigError` or `YouTubeError`. `main`
catches them and exits with code 1. Progress and warnings go to **stderr**,
results to **stdout**.

**Run order in `main`:** config (no network) → artist → iTunes catalog →
resolve YouTube channels (fails fast on a bad key or quota) → Shazam → YouTube
uploads/stats → match → sort → render. If YouTube fails after Shazam
succeeded, print the Shazam results and a warning. `collect_stats` returns
tracks in **catalog order**; matching relies on it ("first row wins"), so sort
only at the end.

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
- **Lossy titles.** The song page `<title>` drops version suffixes
  ("NN (MIXED)" reads "NN"). When YouTube is collected, `add_youtube` replaces
  titles with iTunes track names (same Apple song ids) before matching.
- **Duplicate artist profiles.** One artist can have several Apple artist ids
  with the same name (for PERSONAL ANIMOSITY, `1869261655` holds only
  *Time Rift (Volume 3)*). The name search picks the first match and prints
  the others.

## YouTube quirks (verified 2026-10-17)

- **Topic uploads playlist is complete.** For PERSONAL ANIMOSITY,
  `UUW0XqjvAVf_zimSmZ9MzTaA` lists 321 videos for ~319 catalog tracks.
  `statistics.videoCount` on Topic channels says 0; don't rely on it.
- **Duplicate Topic channels.** The artist has two channels titled
  "PERSONAL ANIMOSITY - Topic": `UCW0XqjvAVf_zimSmZ9MzTaA` (all uploads) and
  `UCExcanjCPOj2O8CKzHas8Fg` (none). The search keeps every exact-title match.
  An empty channel's uploads playlist answers `404 playlistNotFound`, which
  `list_uploads` treats as empty.
- **Topic video titles are the bare track names** (no artist prefix), so the
  artist prefix is stripped only from official-channel videos.
- The *Time Rift (Volume 3)* tracks (other Apple profile) are on the main
  Topic channel, so they appear as unmatched videos for `1720235535`. That is
  expected.
- An invalid key returns `400` with reason `badRequest` and
  `details[].reason = API_KEY_INVALID`; quota/API errors are `403`. 4xx is not
  retried; 5xx and network errors are.
- Don't use per-track `search.list` (100 units each). A full run is ~16 units
  with pinned channels, ~116 with the Topic search.

## Conventions

- **Stdlib only.** Don't add third-party dependencies (the `.env` parser is in
  `config.py`; don't use `python-dotenv`).
- **Tests are offline.** Mock `_fetch` / pipeline functions with
  `unittest.mock`. Tests of `main` must mock `load_config` so they never read
  the real `.env`. Test fixtures copy the *shape* of real pages; hashed CSS
  class suffixes change per build, so never match on them.
- **Don't fail silently.** Bot challenges, markup changes, skipped tracks and
  unmatched videos must be visible (error or warning), never a quiet 0.
- **Exact title matching only.** No fuzzy matching: a wrong match is worse
  than a miss.
- **Never leak the API key.** Pass every URL through `youtube.redact` before it
  reaches an error or log. `.env` is git-ignored.
- **Be polite to shazam.com.** Keep the default concurrency (8 workers) and
  backoff.
- Match the existing style: small functions, `# ---` section banners,
  comments that explain *why*.
- The user often writes in Russian. Answer in the language they use. Code,
  comments, commits and docs stay in English.
