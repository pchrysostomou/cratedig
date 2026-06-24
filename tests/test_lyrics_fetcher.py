"""Phase 4 tests for the LRCLIB lyrics fetcher — no real network (requests mocked)."""

from __future__ import annotations

import pytest
import requests

from cratedig.lyrics import lyrics_fetcher as mod
from cratedig.lyrics.lyrics_fetcher import USER_AGENT, _strip_lrc, fetch_lyrics
from cratedig.models import Track


def _track(title="Song", artists=("Artist A",), album="Album", duration_ms=200_000):
    return Track(
        title=title,
        artists=list(artists),
        album=album,
        isrc=None,
        duration_ms=duration_ms,
        track_number=1,
        disc_number=1,
        release_year=None,
        cover_art_url=None,
        spotify_id="sid",
    )


class FakeResp:
    def __init__(self, status_code=200, payload=None, raise_json=False):
        self.status_code = status_code
        self._payload = payload
        self._raise_json = raise_json

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


# -- /get primary -----------------------------------------------------------


def test_get_plain_lyrics_with_seconds_and_user_agent(mocker):
    captured = {}

    def handler(url, params=None, headers=None, timeout=None):
        captured.update(url=url, params=params, headers=headers)
        return FakeResp(200, {"instrumental": False, "plainLyrics": "la la la"})

    mocker.patch.object(mod.requests, "get", side_effect=handler)

    assert fetch_lyrics(_track(duration_ms=200_000)) == "la la la"
    assert captured["url"].endswith("/get")
    assert captured["params"]["duration"] == 200  # SECONDS, not ms
    assert captured["headers"]["User-Agent"] == USER_AGENT


def test_get_synced_only_is_stripped(mocker):
    payload = {
        "instrumental": False,
        "plainLyrics": None,
        "syncedLyrics": "[ar: A]\n[00:12.34] hello\n[00:15.00] world\n[00:18.00]\n",
    }
    mocker.patch.object(mod.requests, "get", return_value=FakeResp(200, payload))
    assert fetch_lyrics(_track()) == "hello\nworld"


def test_get_instrumental_returns_none(mocker):
    payload = {"instrumental": True, "plainLyrics": None, "syncedLyrics": None}
    mocker.patch.object(mod.requests, "get", return_value=FakeResp(200, payload))
    assert fetch_lyrics(_track()) is None


# -- /search fallback -------------------------------------------------------


def test_404_falls_back_to_search_closest_duration(mocker):
    def handler(url, params=None, headers=None, timeout=None):
        if url.endswith("/get"):
            return FakeResp(404)
        return FakeResp(
            200,
            [
                {"duration": 100, "plainLyrics": "wrong", "instrumental": False},
                {"duration": 201, "plainLyrics": "right", "instrumental": False},
                {"duration": 400, "plainLyrics": "far", "instrumental": False},
            ],
        )

    mocker.patch.object(mod.requests, "get", side_effect=handler)
    assert fetch_lyrics(_track(duration_ms=200_000)) == "right"  # closest to 200s


def test_404_then_empty_search_returns_none(mocker):
    def handler(url, params=None, headers=None, timeout=None):
        return FakeResp(404) if url.endswith("/get") else FakeResp(200, [])

    mocker.patch.object(mod.requests, "get", side_effect=handler)
    assert fetch_lyrics(_track()) is None


# -- soft fail (never raises) ----------------------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        requests.Timeout("timed out"),
        requests.ConnectionError("refused"),
    ],
)
def test_soft_fail_on_request_exceptions(mocker, failure):
    mocker.patch.object(mod.requests, "get", side_effect=failure)
    assert fetch_lyrics(_track()) is None


def test_soft_fail_on_server_error(mocker):
    mocker.patch.object(mod.requests, "get", return_value=FakeResp(500))
    assert fetch_lyrics(_track()) is None


def test_soft_fail_on_invalid_json(mocker):
    mocker.patch.object(mod.requests, "get", return_value=FakeResp(200, raise_json=True))
    assert fetch_lyrics(_track()) is None


# -- _strip_lrc -------------------------------------------------------------


def test_strip_lrc_removes_timestamps_and_metadata():
    text = "[ar: Artist]\n[ti: Song]\n[00:12.34] hello\n[00:15.00] world\n[00:18.00]\n"
    assert _strip_lrc(text) == "hello\nworld"


def test_strip_lrc_preserves_plain_lines():
    assert _strip_lrc("just\nplain\nlines") == "just\nplain\nlines"


def test_strip_lrc_edge_cases():
    assert _strip_lrc("[00:12.00][00:15.00] chorus") == "chorus"  # stacked timestamps
    assert _strip_lrc("[00:12.345] hi") == "hi"  # 3-digit fraction
    assert _strip_lrc("[00:12:34] hey") == "hey"  # colon-separated fraction


def test_synced_that_strips_to_empty_returns_none(mocker):
    payload = {"instrumental": False, "plainLyrics": None, "syncedLyrics": "[ar: A]\n[00:18.00]\n"}
    mocker.patch.object(mod.requests, "get", return_value=FakeResp(200, payload))
    assert fetch_lyrics(_track()) is None


# -- soft fail on malformed JSON shapes (handled by the outer try/except) ----


@pytest.mark.parametrize("payload", [[1, 2, 3], None, "a string", {"instrumental": False}])
def test_get_malformed_200_returns_none(mocker, payload):
    mocker.patch.object(mod.requests, "get", return_value=FakeResp(200, payload))
    assert fetch_lyrics(_track()) is None


@pytest.mark.parametrize(
    "search_payload",
    [
        {"not": "a list"},  # dict instead of list -> min() over keys -> .get fails
        [42, "x"],  # non-dict candidates
        [{"duration": "200", "plainLyrics": "x", "instrumental": False}],  # str duration
    ],
)
def test_search_malformed_returns_none(mocker, search_payload):
    def handler(url, params=None, headers=None, timeout=None):
        return FakeResp(404) if url.endswith("/get") else FakeResp(200, search_payload)

    mocker.patch.object(mod.requests, "get", side_effect=handler)
    assert fetch_lyrics(_track(duration_ms=200_000)) is None


def test_search_candidate_with_none_duration_is_handled(mocker):
    def handler(url, params=None, headers=None, timeout=None):
        if url.endswith("/get"):
            return FakeResp(404)
        return FakeResp(200, [{"duration": None, "plainLyrics": "x", "instrumental": False}])

    mocker.patch.object(mod.requests, "get", side_effect=handler)
    assert fetch_lyrics(_track()) == "x"


def test_duration_ms_none_soft_fails(mocker):
    mocker.patch.object(mod.requests, "get", return_value=FakeResp(200, {"plainLyrics": "x"}))
    assert fetch_lyrics(_track(duration_ms=None)) is None
