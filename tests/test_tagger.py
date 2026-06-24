"""Phase 4 tests for the audio Tagger — no real network, no ffmpeg.

Approach: ``.mp3`` uses a REAL container-agnostic ID3 round-trip on a tiny dummy
file in ``tmp_path`` (``ID3.save`` writes the tag without needing valid MPEG audio
or ffmpeg). The ``.m4a`` path mocks the ``MP4`` class (a real MP4 container cannot
be built without ffmpeg). ``requests.get`` is mocked for cover art.
"""

from __future__ import annotations

import dataclasses

import mutagen
import pytest
import requests
from mutagen.id3 import ID3

from cratedig.exceptions import TaggingError
from cratedig.models import Track
from cratedig.tagging import tagger as tagmod
from cratedig.tagging.tagger import Tagger

_PNG = b"\x89PNG\r\n\x1a\n" + b"fakepngdata"
_JPEG = b"\xff\xd8\xff\xe0" + b"fakejpegdata"


def _track(**over):
    base = {
        "title": "Song",
        "artists": ["Artist A", "Artist B"],
        "album": "Album",
        "isrc": "USABC1234567",
        "duration_ms": 200_000,
        "track_number": 3,
        "disc_number": 1,
        "release_year": "2011",
        "cover_art_url": None,
        "source_id": "sid",
        "lyrics": None,
    }
    base.update(over)
    return Track(**base)


class FakeResp:
    def __init__(self, content=b"", status_code=200):
        self.content = content
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"status {self.status_code}")


def _dummy_mp3(tmp_path):
    p = tmp_path / "song.mp3"
    p.write_bytes(b"\xff\xfb\x90\x64" + b"\x00" * 4096)  # MPEG-ish frame + silence
    return p


# -- mp3 text frames --------------------------------------------------------


def test_mp3_writes_all_text_frames(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")  # cover_art_url is None -> unused
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track())

    tags = ID3(str(p))
    assert tags["TIT2"].text == ["Song"]
    assert tags["TPE1"].text == ["Artist A", "Artist B"]
    assert tags["TALB"].text == ["Album"]
    assert tags["TRCK"].text == ["3"]
    assert tags["TPOS"].text == ["1"]
    assert str(tags["TDRC"].text[0]) == "2011"
    assert tags["TSRC"].text == ["USABC1234567"]
    assert tags.getall("USLT") == []  # no lyrics
    assert tags.getall("APIC") == []  # no cover url


def test_no_release_year_or_isrc_omits_frames(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track(release_year=None, isrc=None))

    tags = ID3(str(p))
    assert tags.getall("TDRC") == []
    assert tags.getall("TSRC") == []


# -- cover art --------------------------------------------------------------


def test_cover_url_none_skips_fetch(tmp_path, mocker):
    get = mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track(cover_art_url=None))

    get.assert_not_called()


def test_mp3_embeds_jpeg_cover(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get", return_value=FakeResp(_JPEG))
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track(cover_art_url="http://img/x.jpg"))

    apic = ID3(str(p)).getall("APIC")
    assert len(apic) == 1
    assert apic[0].mime == "image/jpeg"
    assert apic[0].data == _JPEG


def test_mp3_detects_png_cover(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get", return_value=FakeResp(_PNG))
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track(cover_art_url="http://img/x.png"))

    assert ID3(str(p)).getall("APIC")[0].mime == "image/png"


def test_cover_download_failure_does_not_abort(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get", side_effect=requests.ConnectionError("down"))
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track(cover_art_url="http://img/x.jpg", lyrics="hi"))  # must NOT raise

    tags = ID3(str(p))
    assert tags["TIT2"].text == ["Song"]
    assert tags.getall("USLT")[0].text == "hi"
    assert tags.getall("APIC") == []  # cover skipped, everything else written


# -- lyrics -----------------------------------------------------------------


def test_mp3_embeds_lyrics_when_set(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track(lyrics="hello\nworld"))

    uslt = ID3(str(p)).getall("USLT")
    assert len(uslt) == 1
    assert uslt[0].text == "hello\nworld"


def test_mp3_no_uslt_when_lyrics_none(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track(lyrics=None))

    assert ID3(str(p)).getall("USLT") == []


def test_enriched_frozen_track_lyrics_are_written(tmp_path, mocker):
    # dataclasses.replace on a frozen+slots Track must enrich lyrics correctly (Phase 5 path).
    mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)
    track = _track(lyrics=None)
    enriched = dataclasses.replace(track, lyrics="enriched lyrics")

    assert track.lyrics is None  # original frozen instance unchanged
    Tagger().tag(str(p), enriched)

    assert ID3(str(p)).getall("USLT")[0].text == "enriched lyrics"


# -- error handling ---------------------------------------------------------


def test_mutagen_write_failure_raises_tagging_error(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)
    mocker.patch.object(ID3, "save", side_effect=mutagen.MutagenError("corrupt"))

    with pytest.raises(TaggingError):
        Tagger().tag(str(p), _track())


def test_unexpected_error_is_not_relabeled(tmp_path, mocker):
    # A non-mutagen/non-OS error (e.g. a bug) must surface as itself, not TaggingError.
    mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)
    mocker.patch.object(ID3, "save", side_effect=ValueError("bug"))

    with pytest.raises(ValueError):
        Tagger().tag(str(p), _track())


# -- m4a dispatch (MP4 mocked) ---------------------------------------------


def test_m4a_dispatch_sets_atoms(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")  # no cover

    class FakeMP4(dict):
        def __init__(self, path):
            super().__init__()
            self.saved = False

        def save(self):
            self.saved = True

    fake = FakeMP4("x.m4a")
    mocker.patch.object(tagmod, "MP4", return_value=fake)

    Tagger().tag("whatever.m4a", _track(lyrics="words"))

    assert fake["\xa9nam"] == ["Song"]
    assert fake["\xa9ART"] == ["Artist A", "Artist B"]
    assert fake["\xa9alb"] == ["Album"]
    assert fake["trkn"] == [(3, 0)]
    assert fake["disk"] == [(1, 0)]
    assert fake["\xa9day"] == ["2011"]
    assert fake["\xa9lyr"] == ["words"]
    assert fake.saved


# -- unknown format ---------------------------------------------------------


def test_unknown_extension_never_raises(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")
    p = tmp_path / "song.xyz"
    p.write_bytes(b"not an audio file")

    Tagger().tag(str(p), _track())  # best-effort: must not raise


def test_mp3_no_uslt_when_lyrics_empty_string(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)

    Tagger().tag(str(p), _track(lyrics=""))  # "" is falsy -> no frame

    assert ID3(str(p)).getall("USLT") == []


def test_keyboard_interrupt_propagates_unwrapped(tmp_path, mocker):
    # BaseException must NOT be relabeled as TaggingError.
    mocker.patch.object(tagmod.requests, "get")
    p = _dummy_mp3(tmp_path)
    mocker.patch.object(ID3, "save", side_effect=KeyboardInterrupt)

    with pytest.raises(KeyboardInterrupt):
        Tagger().tag(str(p), _track())


# -- m4a error wrapping + cover (MP4 mocked) -------------------------------


def test_m4a_write_failure_raises_tagging_error(tmp_path, mocker):
    mocker.patch.object(tagmod.requests, "get")

    class FailMP4(dict):
        def __init__(self, path):
            super().__init__()

        def save(self):
            raise mutagen.MutagenError("bad atom")

    mocker.patch.object(tagmod, "MP4", return_value=FailMP4("x.m4a"))

    with pytest.raises(TaggingError):
        Tagger().tag("x.m4a", _track())


@pytest.mark.parametrize(("content", "fmt_attr"), [(_PNG, "FORMAT_PNG"), (_JPEG, "FORMAT_JPEG")])
def test_m4a_embeds_cover_with_correct_format(tmp_path, mocker, content, fmt_attr):
    mocker.patch.object(tagmod.requests, "get", return_value=FakeResp(content))

    class FakeMP4(dict):
        def __init__(self, path):
            super().__init__()

        def save(self):
            pass

    fake = FakeMP4("x.m4a")
    mocker.patch.object(tagmod, "MP4", return_value=fake)

    Tagger().tag("x.m4a", _track(cover_art_url="http://img/cover"))

    assert fake["covr"][0].imageformat == getattr(tagmod.MP4Cover, fmt_attr)
