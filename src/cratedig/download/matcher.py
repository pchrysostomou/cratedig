# cratedig — Copyright (C) 2026 Prodromos Chrysostomou
# Licensed under the GNU General Public License v3.0 or later. See LICENSE.
"""YouTube matcher (Phase 2) — normalize, score, pick the best candidate.

Given a Track and an already-constructed yt-dlp ``YoutubeDL`` instance, search
YouTube and return the watch URL of the best-matching video, or ``None`` if
nothing acceptable is found. This is the accuracy-critical module; see DESIGN.md §6.

``find_best_match`` never raises on "no match" — it returns ``None`` (the
orchestrator translates ``None`` -> ``MatchNotFoundError`` in Phase 5).
"""

from __future__ import annotations

import logging
import re

from rapidfuzz import fuzz

from cratedig.models import Track

logger = logging.getLogger(__name__)

# -- tunable knobs (see DESIGN.md open question #3) -------------------------

SEARCH_RESULTS = 5  # how many YouTube results to request (ytsearchN)
DURATION_TOLERANCE_S = 10.0  # reject candidates farther than this from the track

# Duration is the heaviest signal: a strictly larger additive weight AND a hard gate.
WEIGHT_DURATION = 45.0
WEIGHT_TITLE = 40.0
ARTIST_BONUS = 15.0
TOPIC_CHANNEL_BONUS = 10.0
OFFICIAL_CHANNEL_BONUS = 5.0
MIN_SCORE = 50.0  # floor: reject weak matches (e.g. a duration-only coincidence)

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


def _score(track: Track, entry: dict) -> float | None:
    """Score a candidate, or return ``None`` if it is disqualified.

    Disqualifiers (hard, like a gate): missing/out-of-tolerance duration, or an
    unrequested live/cover/remix/... variant the Spotify title does not share.
    """
    title = entry.get("title")
    channel = _channel_of(entry)
    target = track.duration_ms / 1000  # seconds

    duration = entry.get("duration")
    if not isinstance(duration, (int, float)):  # missing or non-numeric -> disqualify
        logger.info("candidate %r [%s]: no duration -> reject:no-duration", title, channel)
        return None
    delta = abs(duration - target)
    if delta > DURATION_TOLERANCE_S:
        logger.info(
            "candidate %r [%s]: dur=%.0fs target=%.0fs delta=%.0fs > %.0fs -> reject:duration",
            title,
            channel,
            duration,
            target,
            delta,
            DURATION_TOLERANCE_S,
        )
        return None

    raw_title = entry.get("title")
    entry_title = raw_title if isinstance(raw_title, str) else ""

    # Variant gate: an unrequested live/cover/remix/8d/... is never the right track.
    entry_variants = _variant_tokens(entry_title)
    track_variants = _variant_tokens(track.title)
    for word in VARIANT_WORDS:
        phrase = f" {word} "
        if phrase in entry_variants and phrase not in track_variants:
            logger.info(
                "candidate %r [%s]: dur=%.0fs delta=%.0fs variant %r -> reject:variant",
                title,
                channel,
                duration,
                delta,
                word,
            )
            return None

    score = WEIGHT_DURATION * (1 - delta / DURATION_TOLERANCE_S)

    norm_track_title = _normalize(track.title)
    norm_entry_title = _normalize(entry_title)
    score += WEIGHT_TITLE * (fuzz.token_set_ratio(norm_track_title, norm_entry_title) / 100)

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

    logger.info(
        "candidate %r [%s]: dur=%.0fs delta=%.0fs -> score %.1f",
        title,
        channel,
        duration,
        delta,
        score,
    )
    return score


def find_best_match(track: Track, ydl) -> str | None:
    """Search YouTube via ``ydl`` and return the best watch URL, or ``None``."""
    query = track.search_query
    target = track.duration_ms / 1000  # seconds
    search = f"ytsearch{SEARCH_RESULTS}:{query}"
    logger.info("YouTube search: %s (target duration %.0fs)", search, target)

    result = ydl.extract_info(search, download=False)
    entries = (result or {}).get("entries") or []
    logger.info("ytsearch returned %d candidate(s) for %r", len(entries), query)

    best_url: str | None = None
    best_score = float("-inf")
    for entry in entries:
        if not entry or not entry.get("id"):
            logger.info("candidate skipped: missing video id")
            continue
        score = _score(track, entry)  # logs the gate reason for rejected candidates
        if score is None:
            continue
        if score < MIN_SCORE:
            logger.info(
                "candidate %r: score %.1f < %.0f -> reject:below-threshold",
                entry.get("title"),
                score,
                MIN_SCORE,
            )
            continue
        if score > best_score:
            best_score = score
            best_url = f"https://www.youtube.com/watch?v={entry['id']}"

    if best_url is not None:
        logger.info("match %s (score %.1f) for %r", best_url, best_score, query)
    else:
        logger.info("no acceptable match for %r among %d candidate(s)", query, len(entries))
    return best_url
