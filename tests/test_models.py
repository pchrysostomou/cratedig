"""Phase 0 smoke test: the package imports and the data contract behaves."""

import dataclasses

import pytest

import cratedig
from cratedig.models import DownloadResult, ResultStatus, Track


def _make_track(**overrides: object) -> Track:
    base: dict[str, object] = dict(
        title="Midnight City",
        artists=["M83"],
        album="Hurry Up, We're Dreaming",
        isrc="USQX91101101",
        duration_ms=240_000,
        track_number=4,
        disc_number=1,
        release_year="2011",
        cover_art_url="https://example.com/cover.jpg",
        spotify_id="3Df354tabcDEF1234567",
    )
    base.update(overrides)
    return Track(**base)  # type: ignore[arg-type]


def test_package_has_version() -> None:
    assert isinstance(cratedig.__version__, str)
    assert cratedig.__version__


def test_construct_track_and_properties() -> None:
    track = _make_track()
    assert track.primary_artist == "M83"
    assert track.search_query == "M83 - Midnight City"
    assert track.lyrics is None  # enriched later via dataclasses.replace()


def test_primary_artist_falls_back_when_empty() -> None:
    assert _make_track(artists=[]).primary_artist == "Unknown Artist"


def test_track_is_frozen() -> None:
    track = _make_track()
    with pytest.raises(dataclasses.FrozenInstanceError):
        track.title = "Reunion"  # type: ignore[misc]


def test_lyrics_enriched_via_replace() -> None:
    track = _make_track()
    enriched = dataclasses.replace(track, lyrics="waiting in the car")
    assert track.lyrics is None
    assert enriched.lyrics == "waiting in the car"


def test_download_result_defaults() -> None:
    result = DownloadResult(track=_make_track(), status=ResultStatus.SUCCESS)
    assert result.status is ResultStatus.SUCCESS
    assert result.status == "success"  # str-enum
    assert result.output_path is None
    assert result.lyrics_found is False
    assert result.error is None
