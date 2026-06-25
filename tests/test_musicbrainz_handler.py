"""Tests for the MusicBrainz handler — all network mocked (no real MB/CAA)."""

from __future__ import annotations

import json
import logging
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


def _rec(rid, title, artist, score=100, releases=None):
    """A search-result recording candidate."""
    return {
        "id": rid,
        "title": title,
        "score": score,
        "artist-credit": [{"name": artist}],
        "releases": releases or [],
    }


def _detail(rid="looked-up", title="Song", artist="Artist"):
    """A recording-detail payload returned by the `/recording/<id>` lookup."""
    return {
        "id": rid,
        "title": title,
        "length": 200000,
        "artist-credit": [{"name": artist}],
        "isrcs": [],
        "releases": [{"id": REL_MBID, "title": "Album", "date": "2000"}],
    }


def _search_routes(mocker, recordings):
    """Route the `/recording` search to ``recordings``; any lookup returns a stub detail."""
    return _route(
        mocker,
        [
            ("/recording/", FakeResp(_detail())),  # checked first: the lookup
            ("/recording", FakeResp({"recordings": recordings})),  # the search
        ],
    )


def _looked_up(captured):
    return [c["url"] for c in captured["calls"] if "/recording/" in c["url"]]


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


# -- search relevance: query parsing + structured query ---------------------


@pytest.mark.parametrize(
    ("query", "artist", "title"),
    [
        ("Coldplay - Yellow", "Coldplay", "Yellow"),
        ("A - B - C", "A", "B - C"),  # split on the FIRST " - "
        ("Yellow", None, "Yellow"),  # no dash
        ("  spaced  -  out  ", "spaced", "out"),
    ],
)
def test_split_query(query, artist, title):
    assert MusicBrainzHandler._split_query(query) == (artist, title)


def test_lucene_structured_query():
    assert (
        MusicBrainzHandler._lucene("Coldplay", "Yellow")
        == 'recording:"Yellow" AND artist:"Coldplay"'
    )
    assert MusicBrainzHandler._lucene(None, "Get Lucky") == 'recording:"Get Lucky"'


def test_lucene_escapes_quote_and_backslash():
    assert MusicBrainzHandler._lucene(None, 'a"b') == 'recording:"a\\"b"'
    assert MusicBrainzHandler._lucene(None, "a\\b") == 'recording:"a\\\\b"'


def test_search_sends_structured_query(mocker):
    captured = _search_routes(mocker, [_rec("r1", "Yellow", "Coldplay")])

    MusicBrainzHandler().fetch("Coldplay - Yellow")

    search_call = next(c for c in captured["calls"] if "/recording/" not in c["url"])
    assert search_call["params"]["query"] == 'recording:"Yellow" AND artist:"Coldplay"'
    assert search_call["params"]["limit"] == 10


# -- search relevance: artist gate (the core fix) ---------------------------


@pytest.mark.parametrize(
    ("query", "wrong", "right"),
    [
        # The high-score wrong-artist candidate must lose to the real, lower-score one.
        (
            "Coldplay - Yellow",
            _rec("wrong", "Yellow", "moondabor", 100),
            _rec("right", "Yellow", "Coldplay", 80),
        ),
        (
            "Queen - Bohemian Rhapsody",
            _rec("wrong", "Bohemian Rhapsody", "Kyle Landry", 100),
            _rec("right", "Bohemian Rhapsody", "Queen", 70),
        ),
        (
            "Daft Punk - Get Lucky",
            _rec("wrong", "Get Lucky Remix", "HOME", 100),
            _rec("right", "Get Lucky", "Daft Punk", 60),
        ),
    ],
)
def test_artist_gate_picks_real_artist_over_higher_score(mocker, query, wrong, right):
    captured = _search_routes(mocker, [wrong, right])

    MusicBrainzHandler().fetch(query)

    looked_up = _looked_up(captured)
    assert any("/recording/right" in url for url in looked_up)
    assert not any("/recording/wrong" in url for url in looked_up)


def test_artist_gate_empty_returns_empty_and_warns(mocker, caplog):
    captured = _search_routes(mocker, [_rec("cover", "Yellow", "moondabor", 100)])

    with caplog.at_level(logging.WARNING):
        result = MusicBrainzHandler().fetch("Coldplay - Yellow")

    assert result == []
    assert _looked_up(captured) == []  # no recording was fetched
    assert "Coldplay" in caplog.text  # the empty-gate warning is visible


# -- search relevance: variant de-prioritization ----------------------------


def test_variant_deprioritized_for_same_artist(mocker):
    captured = _search_routes(
        mocker,
        [
            _rec("remix", "Get Lucky (Remix)", "Daft Punk", 100),  # higher MB score...
            _rec("studio", "Get Lucky", "Daft Punk", 80),  # ...but the studio cut wins
        ],
    )

    MusicBrainzHandler().fetch("Daft Punk - Get Lucky")

    looked_up = _looked_up(captured)
    assert any("/recording/studio" in url for url in looked_up)
    assert not any("/recording/remix" in url for url in looked_up)


def test_variant_allowed_when_requested(mocker):
    captured = _search_routes(
        mocker,
        [
            _rec("remix", "Get Lucky (Remix)", "Daft Punk", 100),
            _rec("studio", "Get Lucky", "Daft Punk", 80),
        ],
    )

    MusicBrainzHandler().fetch("Daft Punk - Get Lucky Remix")  # user asked for the remix

    assert any("/recording/remix" in url for url in _looked_up(captured))


def test_variant_via_release_group_secondary_type(mocker):
    live = _rec(
        "live",
        "Yellow",
        "Coldplay",
        100,
        releases=[{"release-group": {"secondary-types": ["Live"]}}],
    )
    studio = _rec("studio", "Yellow", "Coldplay", 80)
    captured = _search_routes(mocker, [live, studio])

    MusicBrainzHandler().fetch("Coldplay - Yellow")

    assert any("/recording/studio" in url for url in _looked_up(captured))


def test_no_artist_ranks_by_title_then_variant(mocker):
    captured = _search_routes(
        mocker,
        [
            _rec("live", "Yellow (Live)", "Whoever", 100),
            _rec("studio", "Yellow", "Whoever", 90),
        ],
    )

    MusicBrainzHandler().fetch("Yellow")  # no artist -> no gate

    assert any("/recording/studio" in url for url in _looked_up(captured))


# -- search relevance: deterministic tie-break ------------------------------


@pytest.mark.parametrize("order", [("aaa", "bbb"), ("bbb", "aaa")])
def test_tie_break_is_deterministic_regardless_of_order(mocker, order):
    recs = {rid: _rec(rid, "Yellow", "Coldplay", 100) for rid in ("aaa", "bbb")}
    captured = _search_routes(mocker, [recs[order[0]], recs[order[1]]])

    MusicBrainzHandler().fetch("Coldplay - Yellow")

    # identical on every scored dimension -> the id tie-break makes the pick stable (always "bbb")
    looked_up = _looked_up(captured)
    assert any("/recording/bbb" in url for url in looked_up)
    assert not any("/recording/aaa" in url for url in looked_up)


# -- search relevance: legit artists must not be wrongly filtered -----------


@pytest.mark.parametrize(
    ("query", "credit"),
    [
        ("Beatles - Yesterday", "The Beatles"),  # user omits the leading "The"
        ("The Beatles - Yesterday", "Beatles"),  # ...or MB omits it
        ("Beyonce - Halo", "Beyonce Knowles"),  # requested name is a subset of the credit
        ("Daft Punk - Get Lucky", "Daft Punk feat. Pharrell Williams"),  # feat. credit
    ],
)
def test_artist_gate_accepts_subset_and_prefix_credits(mocker, query, credit):
    title = query.split(" - ", 1)[1]
    captured = _search_routes(mocker, [_rec("hit", title, credit, 90)])

    result = MusicBrainzHandler().fetch(query)

    assert result  # a legitimate artist must NOT be filtered out by the gate
    assert any("/recording/hit" in url for url in _looked_up(captured))


def test_secondary_type_variant_not_allowed_by_substring(mocker):
    # "live" is a substring of "Deliver" but not a whole word, so the Live release must still
    # be de-prioritized -> the studio cut wins despite a higher MB score.
    live = _rec(
        "live",
        "Deliver",
        "Some Band",
        100,
        releases=[{"release-group": {"secondary-types": ["Live"]}}],
    )
    studio = _rec("studio", "Deliver", "Some Band", 80)
    captured = _search_routes(mocker, [live, studio])

    MusicBrainzHandler().fetch("Some Band - Deliver")

    looked_up = _looked_up(captured)
    assert any("/recording/studio" in url for url in looked_up)
    assert not any("/recording/live" in url for url in looked_up)
