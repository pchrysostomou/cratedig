"""Phase 2 tests for the YouTube matcher — all network mocked (no real calls)."""

from __future__ import annotations

import logging

import pytest

from cratedig.download.matcher import (
    MIN_SCORE,
    SEARCH_RESULTS,
    _normalize,
    _score,
    find_best_match,
    rank_candidates,
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


# -- duration as a closeness bonus (not a gate) -----------------------------


def test_duration_is_decisive_between_same_title():
    track = _track(duration_ms=200_000)
    correct = _entry("right", "Artist A - Song", 200, "Artist A - Topic")
    wrong = _entry("off", "Artist A - Song", 207, "Artist A - Topic")  # 7s off -> smaller bonus
    result = find_best_match(track, FakeYDL([wrong, correct]))
    assert result == _url("right")  # closer duration earns the bigger bonus


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
    # Perfect duration + title, but a (Live) tag the track title lacks -> None.
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


def test_remix_accepted_when_track_title_is_remix():
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
        {"id": "a", "title": 123, "duration": 200},  # non-str title -> no title signal
        {"id": "b", "title": "Artist A - Song", "duration": "200"},  # non-numeric duration
        {"id": "c", "title": "Artist A - Song", "duration": 200, "channel": 999},  # non-str chan
    ]
    # never raises; the well-formed candidate still wins
    assert find_best_match(track, FakeYDL([*malformed, good])) == _url("good")
    # a non-str TITLE leaves no signal -> below the floor -> no match
    assert find_best_match(track, FakeYDL([malformed[0]])) is None
    # but a good title with a bad/missing duration is now ACCEPTED (duration is a bonus, not a gate)
    assert find_best_match(track, FakeYDL([malformed[1]])) == _url("b")


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


def test_score_duration_is_positive_bonus_only():
    track = _track(duration_ms=200_000)
    close = _score(track, _entry("a", "Artist A - Song", 200, "Artist A - Topic"))
    far = _score(track, _entry("a", "Artist A - Song", 208, "Artist A - Topic"))
    way_off = _score(track, _entry("a", "Artist A - Song", 280, "Artist A - Topic"))  # delta 80
    no_dur = _score(track, {"id": "a", "title": "Artist A - Song", "channel": "Artist A - Topic"})
    # never rejected for duration; closer just scores higher; missing duration is neutral (0 bonus)
    assert None not in (close, far, way_off, no_dur)
    assert close > far > way_off  # graded closeness bonus, never disqualifying
    assert way_off > no_dur  # a delta-80 still earns a small bonus over no duration at all


def test_score_variant_disqualifies_unless_requested():
    track = _track(title="Song", duration_ms=200_000)
    assert _score(track, _entry("a", "Artist A - Song (Live)", 200)) is None
    remix_track = _track(title="Song (Remix)", duration_ms=200_000)
    assert _score(remix_track, _entry("a", "Artist A - Song (Remix)", 200)) is not None


# -- diagnostic logging (visible under --verbose / INFO) --------------------


def test_diagnostic_logging_reveals_decision(caplog):
    track = _track(title="Song", artists=("Artist A",), duration_ms=200_000)  # target 200s
    ydl = FakeYDL(
        [
            _entry("ok", "Artist A - Song", 200, "Artist A - Topic"),  # delta 0 -> chosen
            _entry("b", "Artist A - Song", 205, "Artist A - Topic"),
            _entry("c", "Artist A - Song", 210, "Artist A - Topic"),
            _entry("loop", "Artist A - Song", 900, "Artist A - Topic"),  # cluster outlier
        ]
    )

    with caplog.at_level(logging.INFO, logger="cratedig.download.matcher"):
        result = find_best_match(track, ydl)

    text = caplog.text
    # search query + target duration are logged
    assert "ytsearch5:Artist A - Song" in text
    assert "target duration 200s" in text
    # the self-calibrating cluster guard ran and shows the closeness bonus per candidate
    assert "candidate cluster median" in text
    assert "dur_bonus=" in text
    # the 900s loop is rejected by the cluster guard, and the final decision is logged
    assert "reject:outlier" in text
    assert f"match {_url('ok')}" in text
    assert result == _url("ok")  # delta 0 -> biggest closeness bonus -> chosen


def test_diagnostic_logging_reports_no_match(caplog):
    track = _track(title="Song", artists=("Artist A",), duration_ms=200_000)
    ydl = FakeYDL([_entry("x", "Totally Unrelated", 200, "")])  # weak title, no artist/channel

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
    assert "target duration unknown" in caplog.text


def test_unknown_target_still_rejects_weak_match():
    # Skipping the duration gate must NOT lower the bar for an unrelated candidate.
    track = _track(title="Get Lucky", artists=("Daft Punk",), duration_ms=0)
    entry = _entry("x", "Some Unrelated Song", 100, "Random Uploader")
    assert find_best_match(track, FakeYDL([entry])) is None


# -- duration no longer gates (FIX A: bonus only, no MB-anchored cutoff) -----


def test_delta_13_lone_candidate_accepted():
    track = _track(title="Song", duration_ms=200_000)
    entry = _entry("ok", "Artist A - Song", 213, "Artist A - Topic")  # delta 13s
    assert find_best_match(track, FakeYDL([entry])) == _url("ok")


def test_delta_39_lone_candidate_accepted():
    track = _track(title="Song", duration_ms=200_000)
    entry = _entry("ok", "Artist A - Song", 239, "Artist A - Topic")  # delta 39s
    assert find_best_match(track, FakeYDL([entry])) == _url("ok")


def test_large_delta_lone_candidate_no_longer_rejected():
    # The old +-60s gate rejected this; with duration a bonus (and no cluster to outlier
    # against), the only correct upload survives even when MB's duration is from another edition.
    track = _track(title="Song", duration_ms=200_000)
    entry = _entry("far", "Artist A - Song", 270, "Artist A - Topic")  # delta 70s
    assert find_best_match(track, FakeYDL([entry])) == _url("far")


# -- soft-duration ranking + rank_candidates --------------------------------


def test_soft_duration_official_beats_zero_off_competitor():
    # The Eminem - Lose Yourself case: MB target 297s; the official upload runs ~31s longer.
    # Soft duration keeps it alive and the channel bonus lets it beat a 0s-off competitor
    # on a no-bonus channel.
    track = _track(title="Lose Yourself", artists=("Eminem",), duration_ms=297_000)
    official = _entry("official", "Eminem - Lose Yourself", 328, "Eminem - Topic")  # delta 31
    other = _entry("other", "Eminem - Lose Yourself", 297, "LyricFind")  # delta 0, no bonus
    assert find_best_match(track, FakeYDL([other, official])) == _url("official")


def test_rank_candidates_orders_best_first_and_excludes_disqualified():
    track = _track(title="Song", artists=("Artist A",), duration_ms=200_000)
    ydl = FakeYDL(
        [
            _entry("random", "Artist A - Song", 200, "Rando"),  # 70
            _entry("topic", "Artist A - Song", 200, "Artist A - Topic"),  # 80
            _entry("live", "Artist A - Song (Live)", 200, "x"),  # variant -> excluded
            _entry("official", "Artist A - Song", 200, "Artist A Official"),  # 75
        ]
    )

    ranked = rank_candidates(track, ydl)

    assert ranked == [_url("topic"), _url("official"), _url("random")]  # best-first, live dropped
    assert find_best_match(track, ydl) == _url("topic")  # top of the ranking


@pytest.mark.parametrize("order", [("aaa", "bbb"), ("bbb", "aaa")])
def test_rank_candidates_deterministic_tiebreak(order):
    track = _track(title="Song", artists=("Artist A",), duration_ms=200_000)
    entries = {
        rid: _entry(rid, "Artist A - Song", 200, "Artist A - Topic") for rid in ("aaa", "bbb")
    }

    ranked = rank_candidates(track, FakeYDL([entries[order[0]], entries[order[1]]]))

    # identical scores -> deterministic id-ascending order regardless of API result order
    assert ranked == [_url("aaa"), _url("bbb")]


# -- FIX A: duration is a bonus, not an MB-anchored gate ---------------------


def test_duration_off_official_now_chosen_was_rejected():
    # The Eminem case: MB target 249s, but the real official upload runs 328s (79s off). The old
    # +-60s gate rejected ALL real uploads; now duration is only a bonus and the upload wins.
    track = _track(title="Lose Yourself", artists=("Eminem",), duration_ms=249_000)
    official = _entry("official", "Eminem - Lose Yourself", 328, "Eminem - Topic")  # 79s off MB
    assert find_best_match(track, FakeYDL([official])) == _url("official")


def test_duration_is_bonus_only_not_a_gate():
    # With a real cluster present, a candidate far from the MB target is still NOT rejected as long
    # as it's within the cluster band; duration only changes the bonus (closer ranks higher).
    track = _track(title="Song", artists=("Artist A",), duration_ms=200_000)  # MB target 200s
    ydl = FakeYDL(
        [
            _entry("far", "Artist A - Song", 320, "Artist A - Topic"),  # 120s off MB, in-cluster
            _entry("mid", "Artist A - Song", 260, "Artist A - Topic"),
            _entry("close", "Artist A - Song", 205, "Artist A - Topic"),  # 5s off
        ]
    )

    ranked = rank_candidates(track, ydl)

    assert set(ranked) == {_url("close"), _url("mid"), _url("far")}  # none rejected for duration
    assert ranked[0] == _url("close")  # closest to MB target earns the biggest bonus


def test_cluster_median_guard_rejects_outliers():
    # No MusicBrainz: the ~320s cluster is the canonical length; a 5718s "Best of" compilation and
    # a 963s medley are >2x the candidate median and rejected, while the real cluster survives.
    track = _track(title="Lose Yourself", artists=("Eminem",), duration_ms=249_000)
    ydl = FakeYDL(
        [
            _entry("a", "Eminem - Lose Yourself", 320, "Eminem - Topic"),
            _entry("b", "Eminem - Lose Yourself", 326, "EminemVEVO"),
            _entry("c", "Eminem - Lose Yourself", 322, "Eminem Official"),
            _entry("comp", "Best of Eminem", 5718, "FanChannel"),  # compilation
            _entry("medley", "Eminem - Lose Yourself", 963, "FanChannel"),  # medley
        ]
    )

    ranked = rank_candidates(track, ydl)

    assert _url("comp") not in ranked and _url("medley") not in ranked  # guard caught both
    assert _url("a") in ranked and _url("b") in ranked and _url("c") in ranked  # cluster kept


def test_cluster_guard_keeps_real_track_when_long_junk_is_majority():
    # Regression for the confirmed-major: 2 real ~320s uploads + 3 hour-long loops. The median
    # sits ON the loops, but the guard rejects only the HIGH side, so the real (shorter) tracks
    # are NOT rejected and win on the duration bonus + title; a 1-hour loop is never the top pick.
    track = _track(title="Lose Yourself", artists=("Eminem",), duration_ms=249_000)
    ydl = FakeYDL(
        [
            _entry("real1", "Eminem - Lose Yourself", 320, "Eminem - Topic"),
            _entry("real2", "Eminem - Lose Yourself", 326, "EminemVEVO"),
            _entry("loop1", "Eminem - Lose Yourself", 3600, "FanA"),
            _entry("loop2", "Eminem - Lose Yourself", 3700, "FanB"),
            _entry("loop3", "Eminem - Lose Yourself", 3800, "FanC"),
        ]
    )

    ranked = rank_candidates(track, ydl)

    assert _url("real1") in ranked and _url("real2") in ranked  # reals NOT rejected
    assert ranked[0] in (_url("real1"), _url("real2"))  # a real upload wins, never a 1-hour loop


def test_cluster_guard_skipped_with_fewer_than_three_candidates():
    # Safety: with only 2 non-variant candidates the median isn't a reliable canonical length, so
    # the guard MUST be skipped — else the correct 320s would be an outlier vs median 660 and get
    # rejected. The correct upload must survive.
    track = _track(title="Lose Yourself", artists=("Eminem",), duration_ms=249_000)
    correct = _entry("correct", "Eminem - Lose Yourself", 320, "Eminem - Topic")
    long_ver = _entry("long", "Eminem - Lose Yourself", 1000, "Eminem - Topic")  # extended cut
    ranked = rank_candidates(track, FakeYDL([correct, long_ver]))

    assert _url("correct") in ranked  # NOT rejected by a guard that should not have run
    assert ranked[0] == _url("correct")  # and it still ranks first (closer to MB target)


# -- FIX B: a dead video in the search results is skipped, not fatal ---------


def test_dead_video_none_entry_skipped_and_survivor_chosen():
    # ignoreerrors=True turns an unavailable video into a None entry; the matcher skips it and the
    # surviving good candidate is still ranked and returned (the search no longer aborts).
    track = _track(title="Get Lucky", artists=("Daft Punk",), duration_ms=248_000)
    ydl = FakeYDL([None, _entry("good", "Daft Punk - Get Lucky", 248, "Daft Punk - Topic")])
    assert find_best_match(track, ydl) == _url("good")
