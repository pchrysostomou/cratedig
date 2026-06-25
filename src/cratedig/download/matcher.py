# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""YouTube matcher (Phase 2) — normalize, score, pick the best candidate.

Given a Track and an already-constructed yt-dlp ``YoutubeDL`` instance, search
YouTube and return the watch URL(s) of the best-matching video(s). This is the
accuracy-critical module; see DESIGN.md §6.

``rank_candidates`` returns best-first watch URLs (``[]`` if nothing is acceptable);
``find_best_match`` returns the single best URL or ``None``. Neither raises on "no
match" — the orchestrator maps an empty ranking to a NOT_FOUND result.
"""

from __future__ import annotations

import logging
import re
import statistics

from rapidfuzz import fuzz

from cratedig.models import Track

logger = logging.getLogger(__name__)

# -- tunable knobs (see DESIGN.md open question #3) -------------------------

SEARCH_RESULTS = 5  # how many YouTube results to request (ytsearchN)

# Duration is NOT a gate. MusicBrainz often reports the length of a different edition than the
# one on YouTube (e.g. "Lose Yourself": MB ~249s, every real upload ~320-328s), so a delta vs the
# MB target must never disqualify a candidate. It only earns a small POSITIVE closeness bonus that
# decays to 0 by this many seconds past the target — a tie-break nudge, never a penalty.
DURATION_BONUS_FALLOFF_S = 90.0

# Outlier guard (no MusicBrainz): reject candidates whose length is wildly LONGER than the MEDIAN
# length of the non-variant candidates — YouTube's own cluster reveals the canonical length, and
# the junk we must drop (hour-long loops, "Best of" compilations, medleys) is always LONGER.
# Only the HIGH side is rejected on purpose: the real track is the shorter one, so a low-side
# cutoff would drop it whenever long junk skews the median past it (a too-short candidate is
# already disfavored by the positive duration bonus). Accepted trade-off: a legitimate >2x edition
# (e.g. a 7-min album cut among 3-min radio cuts) is also dropped. Applied only with
# >= MIN_CLUSTER_FOR_GUARD non-variant candidates; with 1-2 results the median is unreliable and
# could reject the only correct video, so the guard is skipped below that count.
MEDIAN_OUTLIER_HIGH = 2.0  # > 2x the cluster median -> reject (long junk)
MIN_CLUSTER_FOR_GUARD = 3

# Title is the dominant signal; the duration bonus is a small nudge on top.
WEIGHT_DURATION = 15.0  # max duration closeness bonus (at delta=0)
WEIGHT_TITLE = 40.0
ARTIST_BONUS = 15.0
TOPIC_CHANNEL_BONUS = 10.0
OFFICIAL_CHANNEL_BONUS = 5.0
MIN_SCORE = 50.0  # floor: reject weak matches (title similarity must carry real weight)

# A candidate whose title advertises one of these variants is DISQUALIFIED unless
# the Spotify track title itself contains the same term. Matched as a whitespace-
# delimited phrase so "sped up" (two words) and the usual "8D Audio" form are
# detected, and "cover" is never matched inside a word like "discover".
VARIANT_WORDS = ("live", "cover", "remix", "sped up", "nightcore", "8d", "reverb")

_BRACKETS_RE = re.compile(r"[(\[{][^)\]}]*[)\]}]")
_FEAT_RE = re.compile(r"\b(?:feat|ft|featuring)\b\.?")
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, drop bracketed tags + feat keywords + punctuation, collapse ws."""
    if not text:
        return ""
    s = text.lower()
    s = _BRACKETS_RE.sub(" ", s)  # (feat. X), [Official Audio], (Lyrics), (Audio)...
    s = _FEAT_RE.sub(" ", s)  # unify/strip non-bracketed feat / ft / featuring
    s = _NON_WORD_RE.sub(" ", s)  # remove punctuation
    return _WS_RE.sub(" ", s).strip()


def _variant_tokens(text: str) -> str:
    """Space-padded, punctuation-flattened form for whole-phrase membership tests.

    Unlike ``_normalize`` this does NOT drop bracketed content, so variant tags
    such as "(Live)" or "(8D Audio)" remain detectable.
    """
    cleaned = _NON_WORD_RE.sub(" ", text.lower()).strip()
    return f" {cleaned} "


def _channel_of(entry: dict) -> str:
    for key in ("channel", "uploader"):
        value = entry.get(key)
        if isinstance(value, str) and value:
            return value
    return ""


def _duration_of(entry: dict) -> float | None:
    """The candidate's duration in seconds, or ``None`` if missing/non-numeric."""
    value = entry.get("duration")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _is_variant_mismatch(track: Track, entry: dict) -> bool:
    """True if the candidate advertises a live/cover/remix/... the track title does not share."""
    raw_title = entry.get("title")
    entry_title = raw_title if isinstance(raw_title, str) else ""
    entry_variants = _variant_tokens(entry_title)
    track_variants = _variant_tokens(track.title)
    for word in VARIANT_WORDS:
        phrase = f" {word} "
        if phrase in entry_variants and phrase not in track_variants:
            return True
    return False


def _duration_bonus(track: Track, entry: dict) -> float:
    """Positive-only closeness bonus vs the MB target (0 if unknown target / no duration).

    Never negative, never disqualifying — full ``WEIGHT_DURATION`` at delta=0, decaying linearly
    to 0 by ``DURATION_BONUS_FALLOFF_S``. MB duration is unreliable, so this is a nudge only.
    """
    target = track.duration_ms / 1000
    duration = _duration_of(entry)
    if target <= 0 or duration is None:
        return 0.0
    delta = abs(duration - target)
    return WEIGHT_DURATION * max(0.0, 1 - delta / DURATION_BONUS_FALLOFF_S)


def _score(track: Track, entry: dict) -> float | None:
    """Score a candidate (higher is better), or ``None`` if it is a disqualified variant.

    The ONLY hard disqualifier here is an unrequested variant. Duration contributes a
    positive-only bonus and never rejects; the candidate-cluster outlier guard lives in
    :func:`rank_candidates`, which also logs the variant rejection reason.
    """
    if _is_variant_mismatch(track, entry):
        return None

    channel = _channel_of(entry)
    raw_title = entry.get("title")
    entry_title = raw_title if isinstance(raw_title, str) else ""

    norm_track_title = _normalize(track.title)
    norm_entry_title = _normalize(entry_title)
    title_points = WEIGHT_TITLE * (fuzz.token_set_ratio(norm_track_title, norm_entry_title) / 100)
    score = title_points

    haystack = f" {norm_entry_title} {_normalize(channel)} "
    norm_artists = [a for a in (_normalize(x) for x in track.artists) if a]
    if norm_artists:
        present = sum(1 for a in norm_artists if f" {a} " in haystack)
        score += ARTIST_BONUS * (present / len(norm_artists))

    channel_lower = channel.lower().strip()
    if channel_lower.endswith("- topic"):
        score += TOPIC_CHANNEL_BONUS
    elif "official" in channel_lower:
        score += OFFICIAL_CHANNEL_BONUS

    dur_bonus = _duration_bonus(track, entry)
    score += dur_bonus

    duration = _duration_of(entry)
    dur_log = f"dur={duration:.0f}s" if duration is not None else "dur=?"
    logger.info(
        "candidate %r [%s]: %s dur_bonus=%.1f title=%.0f -> score %.1f",
        entry.get("title"),
        channel,
        dur_log,
        dur_bonus,
        title_points,
        score,
    )
    return score


def rank_candidates(track: Track, ydl) -> list[str]:
    """Search YouTube via ``ydl`` and return acceptable candidates (score >= MIN_SCORE) as watch
    URLs, best-first. Deterministic: ties break by video id. The orchestrator tries these in
    order, falling back on the next if a download fails."""
    query = track.search_query
    target = track.duration_ms / 1000
    search = f"ytsearch{SEARCH_RESULTS}:{query}"
    if target > 0:
        logger.info("YouTube search: %s (target duration %.0fs)", search, target)
    else:
        logger.info("YouTube search: %s (target duration unknown)", search)

    result = ydl.extract_info(search, download=False)
    if not result:
        # ignoreerrors=True can turn a TOTAL search failure (network/block/rate-limit) into a None
        # result; surface it as a WARNING so it is not silently read as a plain "no match".
        logger.warning(
            "YouTube search returned no result for %r (unavailable or transient error)", query
        )
    entries = (result or {}).get("entries") or []
    logger.info("ytsearch returned %d candidate(s) for %r", len(entries), query)
    entries = [e for e in entries if isinstance(e, dict) and e.get("id")]

    # Self-calibrating outlier guard: the canonical length is the MEDIAN duration of the
    # non-variant candidates (YouTube's own cluster, not MusicBrainz). Only trust it with a real
    # cluster — with 1-2 candidates the median is unreliable and could reject the only correct one.
    cluster = [
        d
        for e in entries
        if not _is_variant_mismatch(track, e) and (d := _duration_of(e)) is not None and d > 0
    ]
    median = statistics.median(cluster) if len(cluster) >= MIN_CLUSTER_FOR_GUARD else None
    if median is not None:
        logger.info(
            "candidate cluster median duration %.0fs (n=%d); reject > %.0fs",
            median,
            len(cluster),
            MEDIAN_OUTLIER_HIGH * median,
        )
    else:
        logger.info(
            "cluster guard skipped (%d non-variant candidate(s) with duration < %d)",
            len(cluster),
            MIN_CLUSTER_FOR_GUARD,
        )

    scored: list[tuple[float, str]] = []
    for entry in entries:
        if _is_variant_mismatch(track, entry):  # checked first so the log reason is accurate
            logger.info(
                "candidate %r [%s]: -> reject:variant", entry.get("title"), _channel_of(entry)
            )
            continue
        duration = _duration_of(entry)
        if median is not None and duration is not None and duration > MEDIAN_OUTLIER_HIGH * median:
            logger.info(
                "candidate %r: dur=%.0fs > %.1fx cluster median %.0fs -> reject:outlier",
                entry.get("title"),
                duration,
                MEDIAN_OUTLIER_HIGH,
                median,
            )
            continue
        score = _score(track, entry)  # logs the scored line
        if score is None:  # safety: variants already filtered above
            continue
        if score < MIN_SCORE:
            logger.info(
                "candidate %r: score %.1f < %.0f -> reject:below-threshold",
                entry.get("title"),
                score,
                MIN_SCORE,
            )
            continue
        scored.append((score, entry["id"]))

    scored.sort(key=lambda s: (-s[0], s[1]))  # score desc, then id asc (deterministic)
    urls = [f"https://www.youtube.com/watch?v={vid}" for _, vid in scored]

    if urls:
        logger.info(
            "match %s (score %.1f) for %r (%d ranked candidate(s))",
            urls[0],
            scored[0][0],
            query,
            len(urls),
        )
    else:
        logger.info("no acceptable match for %r among %d candidate(s)", query, len(entries))
    return urls


def find_best_match(track: Track, ydl) -> str | None:
    """The single best watch URL, or ``None`` — a thin wrapper over ``rank_candidates``."""
    ranked = rank_candidates(track, ydl)
    return ranked[0] if ranked else None
