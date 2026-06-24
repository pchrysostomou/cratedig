# cratedig — Claude Code Working Agreement

`cratedig` is a Python 3.10+ CLI that takes a Spotify track/album/playlist URL, fetches
metadata (Spotipy), finds the matching audio on YouTube (yt-dlp), transcodes to MP3
(FFmpeg), and embeds ID3 tags + cover art + lyrics (Mutagen + LRCLIB). CLI command: `crate`.

**`DESIGN.md` is the single source of truth for architecture and the phased build plan.
Read it before any work. This file is only the working agreement + conventions.**

## Workflow — ALWAYS follow
1. For every task, FIRST present a short plan (files you'll touch + approach) and WAIT for
   my explicit approval. Do NOT write code before I approve.
2. After approval, implement ONLY the current phase/feature. Do not jump ahead.
3. Run the relevant tests and verify it works before calling it done.
4. Only once it works do you commit (see Git conventions). One phase = one focused PR.
5. Phases are defined in `DESIGN.md` §11. Build them in order.

## Git & commits
- Branch per phase: `feature/<short-name>`. NEVER commit directly to `main`. I open/merge
  PRs myself on GitHub. (Exception: the initial Phase 0 scaffold commit may go on `main`.)
- Commits authored as **Prodromos Chrysostomou**. NO AI attribution or co-author trailers.
- Conventional Commits: `feat:`, `fix:`, `test:`, `chore:`, `docs:`. Scoped, meaningful.

## Stack & layout
- `src/cratedig/` package (`src` layout). Module map: `DESIGN.md` §3.
- Typer + Rich (CLI) · Spotipy · yt-dlp · Mutagen · Pydantic v2 · requests (LRCLIB) · rapidfuzz.
- Tests: pytest with ALL network mocked — no real Spotify / YouTube / LRCLIB calls in tests.
- Lint/format: ruff.

## Non-negotiable rules
- NEVER bundle Spotify credentials. Prompt-per-user; cache under `%APPDATA%\cratedig\`.
- Lyrics (LRCLIB) and tagging are SOFT-FAIL: a miss / 404 / timeout returns `None` and
  never raises into the pipeline.
- Per-track failures become a `DownloadResult`, never abort the whole batch.
- FFmpeg is an external binary, NOT a pip dependency.
- Default 2–3 download workers with random jitter (YouTube anti-bot). Support
  `--cookies-from-browser`.
- Commit only after the feature runs and tests pass.
