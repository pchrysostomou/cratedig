"""Tests for the MusicBrainz handler — all network mocked (no real MB/CAA)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from cratedig.exceptions import InvalidUrlError, ProviderApiError
from cratedig.models import Track
from cratedig.providers import musicbrainz_handler as mb
from cratedig.providers.musicbrainz_handler import USER_AGENT, MusicBrainzHandler

FIXTURES = Path(__file__).parent / "fixtures"

REC_MBID = "11111111-1111-1111-1111-111111111111"
REL_MBID = "22222222-2222-2222-2222-222222222222"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResp:
    def __init__(self, payload=None, status_code=200, raise_json=False):
        self._payload = payload
        self.status_code = status_code
        self._raise_json = raise_json

    def raise_for_status(self):
        if self.status_code >= 400:
            err = requests.HTTPError(f"HTTP {self.status_code}")
            err.response = self
            raise err

    def json(self):
        if self._raise_json:
            raise ValueError("not json")
        return self._payload


@pytest.fixture(autouse=True)
def _no_real_sleep(mocker):
    # Rate limiting would otherwise really sleep ~1s between requests in multi-request paths.
    mocker.patch.object(mb.time, "sleep")


def _route(mocker, routes):
    """Patch requests.get to dispatch by URL substring; record calls. ``routes`` is a
    list of (substring, FakeResp | Exception), checked in order."""
    captured = {"calls": []}

    def handler(url, params=None, headers=None, timeout=None):
        captured["calls"].append({"url": url, "params": params, "headers": headers})
        for substr, resp in routes:
            if substr in url:
                if isinstance(resp, Exception):
                    raise resp
                return resp
        raise AssertionError(f"unexpected URL: {url}")

    mocker.patch.object(mb.requests, "get", side_effect=handler)
    return captured


# -- classification ---------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "kind", "ident"),
    [
        (f"https://musicbrainz.org/recording/{REC_MBID}", "recording", REC_MBID),
        (f"https://musicbrainz.org/release/{REL_MBID}", "release", REL_MBID),
        (f"https://beta.musicbrainz.org/release/{REL_MBID}?foo=1", "release", REL_MBID),
        (REC_MBID, "mbid", REC_MBID),
        ("midnight city m83", "search", "midnight city m83"),
    ],
)
def test_classify(value, kind, ident):
    assert MusicBrainzHandler._classify(value) == (kind, ident)


def test_unknown_entity_url_raises_invalid_url():
    with pytest.raises(InvalidUrlError):
        MusicBrainzHandler._classify("https://musicbrainz.org/artist/" + REC_MBID)


@pytest.mark.parametrize(
    "url",
    [
        f"https://musicbrainz.org/artist/{REC_MBID}",
        f"https://musicbrainz.org/release-group/{REL_MBID}",  # hyphenated entity must not leak
    ],
)
def test_fetch_unsupported_entity_url_raises_invalid_url(mocker, url):
    # fetch() must raise InvalidUrlError (not ProviderApiError) and make NO request.
    no_net = mocker.patch.object(mb.requests, "get", side_effect=AssertionError("no request"))
    with pytest.raises(InvalidUrlError):
        MusicBrainzHandler().fetch(url)
    no_net.assert_not_called()


# -- search -----------------------------------------------------------------


def test_search_picks_best_score_then_looks_up(mocker):
    captured = _route(
        mocker,
        [
            ("/recording/", FakeResp(_load("mb_recording.json"))),  # lookup (note trailing slash)
            ("/recording", FakeResp(_load("mb_search.json"))),  # search
        ],
    )

    tracks = MusicBrainzHandler().fetch("midnight city")

    assert len(tracks) == 1
    assert tracks[0].title == "Midnight City"
    # best of the search (score 100 + has releases) is the recording MBID, and it was looked up
    assert any(REC_MBID in c["url"] for c in captured["calls"])


def test_empty_search_returns_empty(mocker):
    _route(mocker, [("/recording", FakeResp({"recordings": []}))])
    assert MusicBrainzHandler().fetch("nonexistent track") == []


# -- recording --------------------------------------------------------------


def test_recording_mbid_returns_single_track(mocker):
    _route(mocker, [("/recording/", FakeResp(_load("mb_recording.json")))])

    tracks = MusicBrainzHandler().fetch(REC_MBID)

    assert len(tracks) == 1
    t = tracks[0]
    assert isinstance(t, Track)
    assert t.title == "Midnight City"
    assert t.artists == ["M83"]
    assert t.album == "Hurry Up, We're Dreaming"
    assert t.isrc == "USQX91101101"
    assert t.duration_ms == 240000  # MB length is already ms
    assert t.track_number == 1 and t.disc_number == 1
    assert t.release_year == "2011"
    assert t.cover_art_url == f"https://coverartarchive.org/release/{REL_MBID}/front-500"
    assert t.source_id == REC_MBID
    assert t.lyrics is None


def test_recording_url_returns_single_track(mocker):
    _route(mocker, [("/recording/", FakeResp(_load("mb_recording.json")))])
    tracks = MusicBrainzHandler().fetch(f"https://musicbrainz.org/recording/{REC_MBID}")
    assert len(tracks) == 1 and tracks[0].source_id == REC_MBID


# -- release ----------------------------------------------------------------


def test_release_url_returns_ordered_tracks(mocker):
    _route(mocker, [("/release/", FakeResp(_load("mb_release.json")))])

    tracks = MusicBrainzHandler().fetch(f"https://musicbrainz.org/release/{REL_MBID}")

    assert [t.title for t in tracks] == ["Intro", "Midnight City", "Outro"]
    assert [t.track_number for t in tracks] == [1, 2, 1]
    assert [t.disc_number for t in tracks] == [1, 1, 2]
    assert [t.source_id for t in tracks] == ["rec-a", "rec-b", "rec-c"]
    assert all(t.album == "Hurry Up, We're Dreaming" for t in tracks)
    assert all(t.cover_art_url.endswith(f"/release/{REL_MBID}/front-500") for t in tracks)
    assert tracks[1].artists == ["M83", "Susanne Sundfør"]  # full artist credit, in order


def test_release_bare_mbid_falls_back_after_recording_404(mocker):
    _route(
        mocker,
        [
            ("/recording/", FakeResp(status_code=404)),  # not a recording
            ("/release/", FakeResp(_load("mb_release.json"))),  # ... it's a release
        ],
    )

    tracks = MusicBrainzHandler().fetch(REL_MBID)

    assert [t.title for t in tracks] == ["Intro", "Midnight City", "Outro"]


# -- error handling (fatal -> ProviderApiError) -----------------------------


def test_503_raises_provider_api_error(mocker):
    _route(mocker, [("/recording", FakeResp(status_code=503))])
    with pytest.raises(ProviderApiError):
        MusicBrainzHandler().fetch("rate limited query")


def test_network_error_raises_provider_api_error(mocker):
    _route(mocker, [("/recording", requests.ConnectionError("refused"))])
    with pytest.raises(ProviderApiError):
        MusicBrainzHandler().fetch("some query")


def test_bad_json_raises_provider_api_error(mocker):
    _route(mocker, [("/recording", FakeResp(raise_json=True))])
    with pytest.raises(ProviderApiError):
        MusicBrainzHandler().fetch("some query")


def test_malformed_search_missing_id_raises_provider_api_error(mocker):
    # A recording in the search result lacking "id" -> KeyError must surface as ProviderApiError.
    _route(mocker, [("/recording", FakeResp({"recordings": [{"title": "x", "score": 100}]}))])
    with pytest.raises(ProviderApiError):
        MusicBrainzHandler().fetch("some query")


def test_malformed_recording_missing_id_raises_provider_api_error(mocker):
    _route(mocker, [("/recording/", FakeResp({"title": "x", "length": 1000}))])  # no "id"
    with pytest.raises(ProviderApiError):
        MusicBrainzHandler().fetch(REC_MBID)


def test_non_404_recording_error_propagates_without_release_fallback(mocker):
    captured = _route(
        mocker,
        [
            ("/recording/", FakeResp(status_code=500)),  # not a 404 -> do NOT fall back
            ("/release/", FakeResp(_load("mb_release.json"))),
        ],
    )
    with pytest.raises(ProviderApiError):
        MusicBrainzHandler().fetch(REL_MBID)
    assert not any("/release/" in c["url"] for c in captured["calls"])


# -- mapping edge branches --------------------------------------------------


def test_release_skips_null_recording_entries(mocker):
    payload = {
        "id": REL_MBID,
        "title": "Album",
        "date": "2020",
        "media": [
            {
                "position": 1,
                "tracks": [
                    {"position": 1, "recording": None},  # skipped, not mis-numbered
                    {
                        "position": 2,
                        "recording": {
                            "id": "r2",
                            "title": "Real",
                            "length": 1000,
                            "artist-credit": [{"name": "A"}],
                            "isrcs": [],
                        },
                    },
                ],
            }
        ],
    }
    _route(mocker, [("/release/", FakeResp(payload))])

    tracks = MusicBrainzHandler().fetch(f"https://musicbrainz.org/release/{REL_MBID}")

    assert [t.title for t in tracks] == ["Real"]
    assert tracks[0].track_number == 2  # original position preserved


def test_recording_without_releases_has_empty_album(mocker):
    payload = {
        "id": REC_MBID,
        "title": "Solo",
        "length": 90000,
        "artist-credit": [{"name": "A"}],
        "isrcs": [],
        "releases": [],
    }
    _route(mocker, [("/recording/", FakeResp(payload))])

    (t,) = MusicBrainzHandler().fetch(REC_MBID)

    assert t.album == ""
    assert t.cover_art_url is None
    assert t.release_year is None
    assert t.duration_ms == 90000


# -- request hygiene + rate limiting ---------------------------------------


def test_user_agent_and_fmt_json_sent(mocker):
    captured = _route(mocker, [("/recording/", FakeResp(_load("mb_recording.json")))])

    MusicBrainzHandler().fetch(REC_MBID)

    call = captured["calls"][0]
    assert call["headers"]["User-Agent"] == USER_AGENT
    assert call["params"]["fmt"] == "json"


def test_rate_limiting_enforced_between_requests(mocker):
    sleep = mocker.patch.object(mb.time, "sleep")
    # search path = 2 requests; monotonic: set(0.0), wait-check(0.3), set(0.3)
    mocker.patch.object(mb.time, "monotonic", side_effect=[0.0, 0.3, 0.3])
    _route(
        mocker,
        [
            ("/recording/", FakeResp(_load("mb_recording.json"))),
            ("/recording", FakeResp(_load("mb_search.json"))),
        ],
    )

    MusicBrainzHandler(rate_limit_s=1.0).fetch("midnight city")

    sleep.assert_called_once()
    assert sleep.call_args[0][0] == pytest.approx(0.7)  # 1.0 - 0.3 elapsed
