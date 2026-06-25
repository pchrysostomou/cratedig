"""Phase 2 tests for the YouTube matcher — all network mocked (no real calls)."""

from __future__ import annotations

import logging

import pytest

from cratedig.download.matcher import (
    DURATION_HARD_CUTOFF_S,
    MIN_SCORE,
    SEARCH_RESULTS,
    _normalize,
    _score,
    find_best_match,
)
from cratedig.models import Track


class FakeYDL:
    """Minimal stand-in for a yt-dlp YoutubeDL: records calls, returns entries."""

    def __init__(self, entries):
        self._entries = entries
        self.calls = []

    def extract_info(self, query, download=True):
        # default download=True so a missing download=False in the matcher is caught
        self.calls.append((query, download))
        return {"entries": list(self._entries)}


def _track(title="Song", artists=("Artist A",), duration_ms=200_000):
    return Track(
        title=title,
        artists=list(artists),
        album="Album",
        isrc=None,
        duration_ms=duration_ms,
        track_number=1,
        disc_number=1,
        release_year=None,
        cover_art_url=None,
        source_id="sid",
    )


def _entry(vid, title, duration, channel=""):
    return {"id": vid, "title": title, "duration": duration, "channel": channel}


def _url(vid):
    return f"https://www.youtube.com/watch?v={vid}"


# -- happy path / query -----------------------------------------------------


def test_returns_watch_url_for_best_candidate():
    ydl = FakeYDL([_entry("vid123", "Artist A - Song", 200, "Artist A - Topic")])
    assert find_best_match(_track(), ydl) == _url("vid123")


def test_uses_search_query_and_disables_download():
    ydl = FakeYDL([_entry("vid123", "Artist A - Song", 200)])
    find_best_match(_track(), ydl)
    assert ydl.calls == [(f"ytsearch{SEARCH_RESULTS}:Artist A - Song", False)]


# -- duration gate ----------------------------------------------------------


def test_duration_is_decisive_between_same_title():
    track = _track(duration_ms=200_000)
    correct = _entry("right", "Artist A - Song", 200, "Artist A - Topic")
    wrong = _entry("off", "Artist A - Song", 207, "Artist A - Topic")  # 7s off, still in tol
    result = find_best_match(track, FakeYDL([wrong, correct]))
    assert result == _url("right")


def test_returns_none_when_all_beyond_cutoff():
    track = _track(duration_ms=200_000)
    over = DURATION_HARD_CUTOFF_S + 5
    entries = [
        _entry("a", "Artist A - Song", 200 - over),
        _entry("b", "Artist A - Song", 200 + over),
    ]
    assert find_best_match(track, FakeYDL(entries)) is None


# -- normalization ----------------------------------------------------------


def test_normalization_matches_feat_and_brackets():
    track = _track(title="Song", artists=("Artist A", "Artist B"))
    entry = _entry(
        "norm",
        "Artist A - Song (feat. Artist B) [Official Audio]",
        200,
        "Artist A - Topic",
    )
    assert find_best_match(track, FakeYDL([entry])) == _url("norm")


# -- variant rule (hard disqualify, both ways) ------------------------------


def test_variant_entry_rejected_in_favor_of_studio():
    track = _track(title="Song")
    studio = _entry("studio", "Artist A - Song", 200, "Artist A - Topic")
    live = _entry("live", "Artist A - Song (Live)", 200, "Artist A - Topic")
    result = find_best_match(track, FakeYDL([live, studio]))
    assert result == _url("studio")


def test_perfect_live_entry_still_disqualified_for_studio_track():
    # Perfect duration + title, but a (Live) tag the Spotify title lacks -> None.
    track = _track(title="Song", duration_ms=200_000)
    live = _entry("live", "Artist A - Song (Live)", 200, "Artist A - Topic")
    assert find_best_match(track, FakeYDL([live])) is None


@pytest.mark.parametrize(
    "bad_title",
    [
        "Artist A - Song (Live)",
        "Artist A - Song (Remix)",
        "Artist A - Song (8D Audio)",
        "Artist A - Song (Sped Up)",
        "Artist A - Song (Nightcore)",
        "Artist A - Song (Cover)",
        "Artist A - Song (Reverb)",
    ],
)
def test_variants_disqualified_for_studio_track(bad_title):
    track = _track(title="Song", duration_ms=200_000)
    entry = _entry("x", bad_title, 200, "Artist A - Topic")
    assert find_best_match(track, FakeYDL([entry])) is None


def test_remix_accepted_when_spotify_title_is_remix():
    track = _track(title="Song (Club Remix)", duration_ms=200_000)
    entry = _entry("remix", "Artist A - Song (Club Remix)", 200, "Artist A - Topic")
    assert find_best_match(track, FakeYDL([entry])) == _url("remix")


def test_variant_substring_not_false_positive():
    # "cover" must not be detected inside "discover"; this studio entry is valid.
    track = _track(title="Discover", duration_ms=200_000)
    entry = _entry("disc", "Artist A - Discover", 200, "Artist A - Topic")
    assert find_best_match(track, FakeYDL([entry])) == _url("disc")


# -- channel bonus ----------------------------------------------------------


def test_topic_channel_preferred_over_random_uploader():
    track = _track(title="Song")
    topic = _entry("topic", "Artist A - Song", 200, "Artist A - Topic")
    random_up = _entry("rand", "Artist A - Song", 200, "Some Random Uploader")
    result = find_best_match(track, FakeYDL([random_up, topic]))
    assert result == _url("topic")


# -- robustness -------------------------------------------------------------


def test_skips_none_and_idless_entries():
    track = _track()
    entries = [
        None,
        {"title": "Artist A - Song", "duration": 200},  # no id
        _entry("good", "Artist A - Song", 200, "Artist A - Topic"),
    ]
    assert find_best_match(track, FakeYDL(entries)) == _url("good")


def test_returns_none_on_empty_entries():
    assert find_best_match(_track(), FakeYDL([])) is None


def test_returns_none_when_extract_info_returns_none():
    class NoneYDL:
        def extract_info(self, query, download=True):
            return None

    assert find_best_match(_track(), NoneYDL()) is None


def test_weak_match_below_floor_is_rejected():
    # Right duration but unrelated title and no artist/channel signal -> below MIN_SCORE.
    track = _track(title="Completely Different Song Title", artists=("Nobody Here",))
    entry = _entry("weak", "Xyz", 200, "")
    assert find_best_match(track, FakeYDL([entry])) is None


def test_malformed_entries_do_not_raise():
    track = _track()
    good = _entry("good", "Artist A - Song", 200, "Artist A - Topic")
    malformed = [
        {"id": "a", "title": 123, "duration": 200},  # non-str title
        {"id": "b", "title": "Artist A - Song", "duration": "200"},  # non-numeric duration
        {"id": "c", "title": "Artist A - Song", "duration": 200, "channel": 999},  # non-str chan
    ]
    # never raises; the well-formed candidate still wins
    assert find_best_match(track, FakeYDL([*malformed, good])) == _url("good")
    # entries that are malformed where it matters (title/duration) yield no match
    assert find_best_match(track, FakeYDL(malformed[:2])) is None


def test_short_artist_name_not_matched_inside_word():
    # 'AJ' must not count as present inside 'Major'; with no real title/artist
    # signal the candidate then falls below MIN_SCORE and is rejected.
    track = _track(title="Zzz", artists=("AJ",), duration_ms=200_000)
    entry = _entry("x", "Major Lazer - Something", 200, "")
    assert find_best_match(track, FakeYDL([entry])) is None


# -- pure helpers -----------------------------------------------------------


def test_normalize():
    assert _normalize("Artist A - Song (feat. Artist B) [Official Audio]") == "artist a song"
    assert _normalize("Song (Lyrics)") == "song"
    assert _normalize("Song ft. Guy") == "song guy"
    assert _normalize("Hello,   World!!") == "hello world"
    assert _normalize("") == ""


def test_score_duration_gate_and_ordering():
    track = _track(duration_ms=200_000)
    close = _score(track, _entry("a", "Artist A - Song", 200, "Artist A - Topic"))
    far = _score(track, _entry("a", "Artist A - Song", 208, "Artist A - Topic"))
    assert close is not None and far is not None
    assert close > far  # closer duration scores higher
    assert _score(track, _entry("a", "Artist A - Song", 280)) is None  # delta 80 > 60 cutoff
    assert _score(track, {"id": "a", "title": "Artist A - Song"}) is None  # missing duration


def test_score_variant_disqualifies_unless_requested():
    track = _track(title="Song", duration_ms=200_000)
    assert _score(track, _entry("a", "Artist A - Song (Live)", 200)) is None
    remix_track = _track(title="Song (Remix)", duration_ms=200_000)
    assert _score(remix_track, _entry("a", "Artist A - Song (Remix)", 200)) is not None


# -- diagnostic logging (visible under --verbose / INFO) --------------------


def test_diagnostic_logging_reveals_decision(caplog):
    track = _track(title="Song", duration_ms=200_000)  # target 200s
    ydl = FakeYDL(
        [
            _entry("ok", "Artist A - Song", 200, "Artist A - Topic"),  # delta 0 -> chosen
            _entry("toolong", "Artist A - Song", 400, "Artist A - Topic"),  # delta 200 -> gated
        ]
    )

    with caplog.at_level(logging.INFO, logger="cratedig.download.matcher"):
        result = find_best_match(track, ydl)

    text = caplog.text
    # search query + target duration are logged
    assert "ytsearch5:Artist A - Song" in text
    assert "target duration 200s" in text
    # the out-of-tolerance candidate logs its duration, delta, and the gate reason
    assert "dur=400s" in text and "delta=200s" in text and "reject:duration" in text
    # the final decision is logged
    assert f"match {_url('ok')}" in text
    # behavior is unchanged: the correct candidate is still returned
    assert result == _url("ok")


def test_diagnostic_logging_reports_no_match(caplog):
    track = _track(title="Song", duration_ms=200_000)
    ydl = FakeYDL([_entry("x", "Artist A - Song", 400, "Artist A - Topic")])  # all out of tolerance

    with caplog.at_level(logging.INFO, logger="cratedig.download.matcher"):
        result = find_best_match(track, ydl)

    assert result is None
    assert "no acceptable match" in caplog.text


# -- Bug A: unknown/zero target duration skips the duration gate -------------


def test_unknown_target_skips_gate_and_good_match_clears_threshold(caplog):
    # MusicBrainz returned no length -> duration_ms=0 (target unknown). A correct title +
    # artist + "- Topic" channel must clear MIN_SCORE despite the neutral (0) duration component,
    # and a candidate that WOULD be far past tolerance must NOT be rejected for duration.
    track = _track(title="Get Lucky", artists=("Daft Punk",), duration_ms=0)
    entry = _entry("gl", "Daft Punk - Get Lucky", 248, "Daft Punk - Topic")  # 248s vs 0s target

    assert _score(track, entry) >= MIN_SCORE  # good non-duration match clears the floor

    with caplog.at_level(logging.INFO, logger="cratedig.download.matcher"):
        result = find_best_match(track, FakeYDL([entry]))

    assert result == _url("gl")  # not rejected for duration; chosen on title/artist/channel
    assert "duration gate skipped (unknown target)" in caplog.text


def test_unknown_target_still_rejects_weak_match():
    # Skipping the duration gate must NOT lower the bar for an unrelated candidate.
    track = _track(title="Get Lucky", artists=("Daft Punk",), duration_ms=0)
    entry = _entry("x", "Some Unrelated Song", 100, "Random Uploader")
    assert find_best_match(track, FakeYDL([entry])) is None


# -- wide (60s) soft-duration cutoff ----------------------------------------


def test_delta_13_within_cutoff_accepted():
    track = _track(title="Song", duration_ms=200_000)
    entry = _entry("ok", "Artist A - Song", 213, "Artist A - Topic")  # delta 13s
    assert find_best_match(track, FakeYDL([entry])) == _url("ok")


def test_delta_39_now_accepted():
    # The real bug: a ~39s-off official upload (intro/outro) was rejected by the old +-20s gate;
    # within the 60s soft cutoff it now survives and, as the only candidate, is returned.
    track = _track(title="Song", duration_ms=200_000)
    entry = _entry("ok", "Artist A - Song", 239, "Artist A - Topic")  # delta 39s
    assert find_best_match(track, FakeYDL([entry])) == _url("ok")


def test_delta_beyond_cutoff_rejected():
    track = _track(title="Song", duration_ms=200_000)
    entry = _entry("far", "Artist A - Song", 270, "Artist A - Topic")  # delta 70s > 60
    assert find_best_match(track, FakeYDL([entry])) is None


def test_cutoff_boundary_inclusive_at_60s():
    track = _track(title="Song", duration_ms=200_000)
    at = _entry("at", "Artist A - Song", 260, "Artist A - Topic")  # delta 60s -> accepted
    over = _entry("over", "Artist A - Song", 261, "Artist A - Topic")  # delta 61s -> rejected
    assert find_best_match(track, FakeYDL([at])) == _url("at")
    assert find_best_match(track, FakeYDL([over])) is None
