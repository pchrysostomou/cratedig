"""Phase 1 tests for SpotifyHandler — all network mocked (no real API calls)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests
from spotipy import SpotifyException

from cratedig.exceptions import InvalidUrlError, SpotifyApiError
from cratedig.models import Track
from cratedig.providers import spotify_handler as sh_module
from cratedig.providers.spotify_handler import SpotifyHandler

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
def handler(mocker) -> SpotifyHandler:
    """A SpotifyHandler whose Spotipy client is a MagicMock (no network)."""
    mocker.patch.object(sh_module, "SpotifyClientCredentials")
    mock_client = mocker.MagicMock(name="SpotifyClient")
    mocker.patch.object(sh_module, "Spotify", return_value=mock_client)
    return SpotifyHandler("client-id", "client-secret")


# -- __init__ / auth + retry config ----------------------------------------


def test_init_uses_client_credentials_and_builtin_retry(mocker):
    creds = mocker.patch.object(sh_module, "SpotifyClientCredentials")
    spotify = mocker.patch.object(sh_module, "Spotify")

    SpotifyHandler("cid", "csecret")

    creds.assert_called_once_with(client_id="cid", client_secret="csecret")
    _, kwargs = spotify.call_args
    assert kwargs["auth_manager"] is creds.return_value
    # Built-in retry/backoff configured so 429 + Retry-After is handled by Spotipy.
    assert kwargs["retries"] >= 1
    assert kwargs["status_retries"] >= 1
    assert "backoff_factor" in kwargs


# -- URL / URI parsing ------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "kind", "sid"),
    [
        ("spotify:track:abc123", "track", "abc123"),
        ("spotify:album:def456", "album", "def456"),
        ("spotify:playlist:ghi789", "playlist", "ghi789"),
        ("https://open.spotify.com/track/abc123", "track", "abc123"),
        ("https://open.spotify.com/album/def456?si=xyz", "album", "def456"),
        ("https://open.spotify.com/playlist/ghi789", "playlist", "ghi789"),
        ("https://open.spotify.com/intl-de/track/abc123?si=q", "track", "abc123"),
        ("http://open.spotify.com/track/abc123", "track", "abc123"),
        ("https://open.spotify.com/track/abc123/", "track", "abc123"),
        ("https://open.spotify.com/track/abc123/?si=q", "track", "abc123"),
        ("  spotify:track:abc123  ", "track", "abc123"),
    ],
)
def test_parse_input_valid(value, kind, sid):
    assert SpotifyHandler._parse_input(value) == (kind, sid)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "garbage",
        "https://example.com/track/abc123",
        "spotify:artist:abc123",
        "https://open.spotify.com/artist/abc123",
        "spotify:track:",
        "ftp://open.spotify.com/track/abc123",
        # unsupported kinds
        "spotify:episode:abc123",
        "https://open.spotify.com/show/abc123",
        # plural / prefixed directory must not slip through
        "https://open.spotify.com/playlists/abc123",
        "https://open.spotify.com/trackfoo/abc123",
        # trailing junk / non-base62 must be rejected, not silently truncated
        "https://open.spotify.com/track/abc/123",
        "https://open.spotify.com/track/ab!c",
        "https://open.spotify.com/track/abc123/extra",
        # host spoofing must be rejected (host is anchored)
        "https://open.spotify.com.evil.com/track/abc123",
        "https://evil.open.spotify.com/track/abc123",
        "https://open.spotify.com@evil.com/track/abc123",
    ],
)
def test_parse_input_invalid_raises(value):
    with pytest.raises(InvalidUrlError):
        SpotifyHandler._parse_input(value)


# -- track fetch + mapping --------------------------------------------------


def test_fetch_track_maps_all_fields(handler):
    handler._sp.track.return_value = _load("track.json")

    tracks = handler.fetch("spotify:track:track1id000000000000001")

    handler._sp.track.assert_called_once_with("track1id000000000000001")
    assert len(tracks) == 1
    t = tracks[0]
    assert isinstance(t, Track)
    assert t.title == "Midnight City"
    assert t.artists == ["M83"]
    assert t.album == "Hurry Up, We're Dreaming"
    assert t.isrc == "USQX91101101"
    assert t.duration_ms == 240000
    assert t.track_number == 4
    assert t.disc_number == 1
    assert t.release_year == "2011"
    assert t.cover_art_url == "https://img/large.jpg"  # first (largest) image
    assert t.spotify_id == "track1id000000000000001"
    assert t.lyrics is None


def test_fetch_track_via_url(handler):
    handler._sp.track.return_value = _load("track.json")

    handler.fetch("https://open.spotify.com/track/track1id000000000000001?si=x")

    handler._sp.track.assert_called_once_with("track1id000000000000001")


def test_mapping_missing_isrc_and_images_yields_none(handler):
    data = _load("track.json")
    data.pop("external_ids", None)
    data["album"]["images"] = []
    handler._sp.track.return_value = data

    (t,) = handler.fetch("spotify:track:track1id000000000000001")

    assert t.isrc is None
    assert t.cover_art_url is None


# -- album fetch + pagination ----------------------------------------------


def test_fetch_album_paginates_in_order(handler):
    handler._sp.album.return_value = _load("album.json")
    handler._sp.next.return_value = _load("album_page2.json")

    tracks = handler.fetch("spotify:album:albumid00000000000001")

    handler._sp.album.assert_called_once_with("albumid00000000000001")
    handler._sp.next.assert_called_once()
    assert [t.title for t in tracks] == ["Intro", "Midnight City", "Wait", "Reunion"]
    assert [t.spotify_id for t in tracks] == ["alb1", "alb2", "alb3", "alb4"]
    # Album name + cover art applied to every track; simplified tracks have no ISRC.
    assert all(t.album == "Hurry Up, We're Dreaming" for t in tracks)
    assert all(t.cover_art_url == "https://img/album.jpg" for t in tracks)
    assert all(t.isrc is None for t in tracks)
    assert all(t.release_year == "2011" for t in tracks)


# -- playlist fetch + pagination + skipping --------------------------------


def test_fetch_playlist_paginates_skips_and_orders(handler):
    handler._sp.playlist_items.return_value = _load("playlist_page1.json")
    handler._sp.next.return_value = _load("playlist_page2.json")

    tracks = handler.fetch("https://open.spotify.com/playlist/plid00000000000000001?si=z")

    handler._sp.playlist_items.assert_called_once()
    args, kwargs = handler._sp.playlist_items.call_args
    assert args[0] == "plid00000000000000001"
    assert kwargs["additional_types"] == ("track",)
    # null + id-less entries skipped; order preserved across both pages.
    assert [t.title for t in tracks] == ["Song A", "Song B"]
    assert [t.spotify_id for t in tracks] == ["plA", "plB"]
    a, b = tracks
    assert a.artists == ["Artist A", "Guest"]  # full artist set, ordered
    assert a.isrc == "AAA111111111"
    assert a.album == "Album A"  # playlist tracks carry their own album
    assert a.cover_art_url == "https://img/a.jpg"
    assert a.release_year == "2019"
    assert b.isrc is None  # empty external_ids
    assert b.cover_art_url is None  # empty images
    assert b.release_year == "2020"
    assert b.disc_number == 2


# -- error handling ---------------------------------------------------------


def test_spotify_api_error_is_wrapped(handler):
    handler._sp.track.side_effect = SpotifyException(429, -1, "rate limited")

    with pytest.raises(SpotifyApiError):
        handler.fetch("spotify:track:abc123")


def test_requests_error_is_wrapped(handler):
    handler._sp.album.side_effect = requests.RequestException("connection reset")

    with pytest.raises(SpotifyApiError):
        handler.fetch("spotify:album:abc123")


def test_invalid_url_is_not_wrapped(handler):
    # InvalidUrlError must surface as-is, never converted to SpotifyApiError.
    with pytest.raises(InvalidUrlError):
        handler.fetch("not-a-spotify-url")
