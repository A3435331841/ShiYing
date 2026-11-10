from io import BytesIO
from pathlib import Path

import pytest

from app.downloader import DownloaderConfig, MediaDownloader
from app.engine import transfer
from app.errors import MediaDownloadError
from app.models import MediaType, Platform
from app.xiaohongshu import RemoteAsset


class _Response:
    def __init__(self, payload: bytes, content_type: str, declared_size: int | None):
        self.stream = BytesIO(payload)
        self.headers = {"Content-Type": content_type}
        if declared_size is not None:
            self.headers["Content-Length"] = str(declared_size)
        self.reads: list[bytes] = []
        self.closed = False

    def read(self, size: int) -> bytes:
        chunk = self.stream.read(min(size, 64 * 1024))
        self.reads.append(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True


class _AssetYoutubeDL:
    def __init__(self, response: _Response):
        self.response = response
        self.requests = []

    def urlopen(self, request):
        self.requests.append(request)
        return self.response


@pytest.fixture(autouse=True)
def _mock_asset_opener(monkeypatch):
    def open_response(ydl, request, *, is_trusted_url):
        assert is_trusted_url(request.url)
        return ydl.urlopen(request)

    monkeypatch.setattr(transfer, "_open_xiaohongshu_response", open_response)


def _download_xhs(engine, ydl, output_dir, media_type, *, asset_size=None, **kwargs):
    host = "sns-img-bd.xhscdn.com" if media_type == MediaType.IMAGE else "sns-video-bd.xhscdn.com"
    return engine._download_first_available_asset(
        ydl,
        [RemoteAsset(candidates=[f"https://{host}/original"], index=1, size=asset_size)],
        output_dir,
        "2025-11-14",
        "Title",
        "note-id",
        "https://www.xiaohongshu.com/explore/note-id",
        platform=Platform.XIAOHONGSHU,
        media_type=media_type,
        callback=None,
        should_cancel=lambda: False,
        asset_index=1,
        **kwargs,
    )


def _base_path(engine, output_dir, extension):
    return engine._xhs_output_path(
        output_dir, "2025-11-14", "Title", "note-id", extension, 1
    )


@pytest.mark.parametrize("size_source", ["content-length", "asset-size"])
def test_same_size_different_content_is_downloaded_and_preserved(tmp_path, size_source):
    payload = b"\xff\xd8\xff" + b"x" * (2 * 1024 * 1024 + 9)
    old = payload[: 1024 * 1024] + b"y" + payload[1024 * 1024 + 1 :]
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    base = _base_path(engine, tmp_path, "jpg")
    base.write_bytes(old)
    response = _Response(
        payload,
        "image/jpeg",
        len(payload) if size_source == "content-length" else None,
    )
    ydl = _AssetYoutubeDL(response)

    path, _ = _download_xhs(
        engine,
        ydl,
        tmp_path,
        MediaType.IMAGE,
        asset_size=len(payload) if size_source == "asset-size" else None,
    )

    assert path == base.with_name(f"{base.stem} (2).jpg").resolve()
    assert base.read_bytes() == old
    assert path.read_bytes() == payload
    assert len(response.reads) > 2 and response.reads[-1] == b""
    assert response.closed
    assert not list(tmp_path.glob(".original-media-*.part"))


def test_matching_existing_size_does_not_hide_incomplete_response(tmp_path):
    payload = b"\xff\xd8\xff" + b"x" * 400
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    base = _base_path(engine, tmp_path, "jpg")
    base.write_bytes(payload)
    response = _Response(payload[:100], "image/jpeg", len(payload))

    with pytest.raises(MediaDownloadError, match="Incomplete media response"):
        _download_xhs(engine, _AssetYoutubeDL(response), tmp_path, MediaType.IMAGE)

    assert base.read_bytes() == payload
    assert response.reads[-1] == b""
    assert response.closed
    assert set(tmp_path.iterdir()) == {base}


def test_existing_video_is_verified_after_full_download_before_reuse(monkeypatch, tmp_path):
    payload = b"\x00\x00\x00\x18ftypisom" + b"verified video"
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    base = _base_path(engine, tmp_path, "mp4")
    base.write_bytes(payload)
    response = _Response(payload, "video/mp4", len(payload))
    verified = []

    def verify(path, asset, *, should_cancel, require_quality_fingerprint):
        assert path != base
        assert path.read_bytes() == payload
        assert response.reads[-1] == b""
        verified.append(path)
        return asset

    monkeypatch.setattr(engine, "_verify_local_video_asset", verify)
    path, _ = _download_xhs(
        engine,
        _AssetYoutubeDL(response),
        tmp_path,
        MediaType.VIDEO,
        verify_declared_dimensions=True,
    )

    assert path == base.resolve()
    assert len(verified) == 1
    assert not verified[0].exists()
    assert set(tmp_path.iterdir()) == {base}


def test_pair_image_does_not_reuse_existing_identical_unpaired_stem(tmp_path):
    payload = b"\xff\xd8\xffstatic image"
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    base = _base_path(engine, tmp_path, "jpg")
    base.write_bytes(payload)
    response = _Response(payload, "image/jpeg", len(payload))

    path, _ = _download_xhs(
        engine, _AssetYoutubeDL(response), tmp_path, MediaType.IMAGE, pair_image=True
    )

    assert path == base.with_name(f"{base.stem} (2).jpg").resolve()
    assert base.read_bytes() == path.read_bytes() == payload
    assert response.reads[-1] == b""


@pytest.mark.parametrize("target_state", ["absent", "identical", "different", "symlink", "broken-symlink"])
def test_paired_motion_uses_only_exact_stem(tmp_path, target_state):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    payload = b"\x00\x00\x00\x18ftypisom" + b"x" * (2 * 1024 * 1024)
    different = payload[: 1024 * 1024] + b"y" + payload[1024 * 1024 + 1 :]
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    base = _base_path(engine, output_dir, "mp4")
    base.write_bytes(payload)  # An identical file at an unrelated stem must be ignored.
    static = output_dir / "paired (2).jpg"
    static.write_bytes(b"static image")
    target = static.with_suffix(".mp4")
    external = tmp_path / "external.mp4"
    if target_state == "identical":
        target.write_bytes(payload)
    elif target_state == "different":
        target.write_bytes(different)
    elif target_state in {"symlink", "broken-symlink"}:
        if target_state == "symlink":
            external.write_bytes(payload)
        try:
            target.symlink_to(external)
        except (OSError, NotImplementedError) as exc:
            pytest.skip(f"File symlinks are unavailable: {exc}")
    response = _Response(payload, "video/mp4", len(payload))
    ydl = _AssetYoutubeDL(response)

    if target_state in {"absent", "identical"}:
        path, _ = _download_xhs(
            engine, ydl, output_dir, MediaType.VIDEO, paired_stem=static.stem
        )
        assert path == target.resolve()
        assert path.stem == static.stem
        assert target.read_bytes() == payload
    else:
        with pytest.raises(MediaDownloadError, match="Paired media destination"):
            _download_xhs(
                engine, ydl, output_dir, MediaType.VIDEO, paired_stem=static.stem
            )
        if target_state == "different":
            assert target.read_bytes() == different
        else:
            assert target.is_symlink()
            if target_state == "symlink":
                assert external.read_bytes() == payload
            else:
                assert not external.exists()
    assert base.read_bytes() == payload
    assert static.read_bytes() == b"static image"
    assert len(ydl.requests) == 1
    assert response.reads[-1] == b"" and response.closed
    assert set(output_dir.iterdir()) == {base, static, target}


@pytest.mark.parametrize("broken", [False, True])
def test_paired_motion_rejects_symlink_even_when_symlinks_cannot_be_created(
    monkeypatch, tmp_path, broken
):
    payload = b"\x00\x00\x00\x18ftypisom" + b"new motion"
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    static = tmp_path / "paired.jpg"
    static.write_bytes(b"static image")
    target = static.with_suffix(".mp4")
    if not broken:
        target.write_bytes(payload)
    real_is_symlink = Path.is_symlink
    real_publish = transfer.publish_no_replace

    def simulated_is_symlink(path):
        return path == target or real_is_symlink(path)

    def simulated_publish(temporary, destination):
        assert destination == target
        if broken:
            raise FileExistsError("simulated dangling symlink")
        return real_publish(temporary, destination)

    monkeypatch.setattr(Path, "is_symlink", simulated_is_symlink)
    monkeypatch.setattr(transfer, "publish_no_replace", simulated_publish)
    response = _Response(payload, "video/mp4", len(payload))

    with pytest.raises(MediaDownloadError, match="Paired media destination"):
        _download_xhs(
            engine, _AssetYoutubeDL(response), tmp_path, MediaType.VIDEO,
            paired_stem=static.stem,
        )
    assert response.reads[-1] == b"" and response.closed
    assert set(tmp_path.iterdir()) == ({static} if broken else {static, target})
    if not broken:
        assert target.read_bytes() == payload


@pytest.mark.parametrize("race_content", ["identical", "different"])
def test_paired_motion_handles_publication_race(monkeypatch, tmp_path, race_content):
    payload = b"\x00\x00\x00\x18ftypisom" + b"new motion"
    competing_payload = payload if race_content == "identical" else b"X" + payload[1:]
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    static = tmp_path / "paired.jpg"
    static.write_bytes(b"static image")
    target = static.with_suffix(".mp4")
    response = _Response(payload, "video/mp4", len(payload))
    ydl = _AssetYoutubeDL(response)
    original_publish = transfer.publish_no_replace
    attempts = []

    def publish_after_competitor(temporary, destination):
        assert destination == target
        assert temporary.read_bytes() == payload
        assert response.reads[-1] == b""
        attempts.append(destination)
        destination.write_bytes(competing_payload)
        return original_publish(temporary, destination)

    monkeypatch.setattr(transfer, "publish_no_replace", publish_after_competitor)
    if race_content == "identical":
        path, _ = _download_xhs(
            engine, ydl, tmp_path, MediaType.VIDEO, paired_stem=static.stem
        )
        assert path == target.resolve()
    else:
        with pytest.raises(MediaDownloadError, match="Paired media destination"):
            _download_xhs(
                engine, ydl, tmp_path, MediaType.VIDEO, paired_stem=static.stem
            )
    assert attempts == [target]
    assert target.read_bytes() == competing_payload
    assert len(ydl.requests) == 1
    assert set(tmp_path.iterdir()) == {static, target}


@pytest.mark.parametrize("target_state", ["identical", "different", "race-identical"])
def test_paired_douyin_probe_reuses_only_identical_exact_target(
    monkeypatch, tmp_path, target_state
):
    payload = b"\x00\x00\x00\x18ftypisom" + b"complete verified probe"
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    probe = tmp_path / ".probe.part"
    probe.write_bytes(payload)
    static = tmp_path / "paired.jpg"
    static.write_bytes(b"static image")
    target = static.with_suffix(".mp4")
    different = b"X" + payload[1:]
    if target_state == "identical":
        target.write_bytes(payload)
    elif target_state == "different":
        target.write_bytes(different)
    else:
        original_publish = transfer.publish_no_replace

        def publish_after_competitor(temporary, destination):
            assert destination == target
            destination.write_bytes(payload)
            return original_publish(temporary, destination)

        monkeypatch.setattr(transfer, "publish_no_replace", publish_after_competitor)
    asset = RemoteAsset(
        candidates=[],
        index=1,
        size=len(payload),
        duration=1.0,
        video_codec="h264",
        probe_prefix_size=12,
        probe_prefix_sha256="a" * 64,
        redirect_source_url="https://v26-web.douyinvod.com/original.mp4",
    )
    monkeypatch.setattr(engine, "_is_trusted_douyin_asset_url", lambda *args: True)
    monkeypatch.setattr(engine, "_take_douyin_probe_file", lambda value: probe)
    verified = []

    def verify(path, value, *, should_cancel, require_quality_fingerprint):
        assert path.read_bytes() == payload
        assert require_quality_fingerprint is True
        verified.append(path)
        return value

    def no_network(*args, **kwargs):
        raise AssertionError("The complete verified probe must not be downloaded again")

    monkeypatch.setattr(engine, "_verify_local_video_asset", verify)
    monkeypatch.setattr(engine, "_open_douyin_media_response", no_network)
    events = []

    def download():
        return engine._download_first_available_asset(
            object(),
            [asset],
            tmp_path,
            "2025-11-14",
            "Title",
            "note-id",
            "https://www.douyin.com/note/123",
            platform=Platform.DOUYIN,
            media_type=MediaType.VIDEO,
            callback=events.append,
            should_cancel=lambda: False,
            asset_index=1,
            progress_index=2,
            progress_count=4,
            verify_declared_dimensions=True,
            require_quality_fingerprint=True,
            paired_stem=static.stem,
        )

    if target_state == "different":
        with pytest.raises(MediaDownloadError, match="Paired media destination"):
            download()
        assert target.read_bytes() == different
    else:
        path, chosen = download()
        assert path == target.resolve()
        assert chosen is asset
        assert target.read_bytes() == payload
        assert [event.progress.percent for event in events] == [25.0, 50.0]
    assert verified == [probe]
    assert not probe.exists()
    assert set(tmp_path.iterdir()) == {static, target}


def test_duplicate_content_length_header_still_enforced_against_truncation(tmp_path):
    """代理合并出的 "N, N" 形态 Content-Length 必须仍被解析并执行完整性检查，
    否则无 Content-Length 判定会跳过校验、把截断响应发布成完整文件。"""

    payload = b"\xff\xd8\xff" + b"x" * 500
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    response = _Response(payload, "image/jpeg", None)
    response.headers["Content-Length"] = f"{len(payload)}, {len(payload)}"
    # 模拟 close 定界响应被服务端提前截断：可读字节远少于声明长度。
    response.stream = BytesIO(payload[:100])
    ydl = _AssetYoutubeDL(response)

    with pytest.raises(MediaDownloadError, match="Incomplete media response"):
        _download_xhs(engine, ydl, tmp_path, MediaType.IMAGE)

    assert not list(tmp_path.glob("*.jpg"))


def test_image_dimensions_beyond_first_chunk_are_still_verified(tmp_path):
    """SOF 被巨型前置段推到首 1MB 之外的合法 JPEG 不应被误判失败。

    旧实现只对首 1MB 解析尺寸，超过即报 "could not be verified"；
    新实现在上限内继续读流累积扫描。
    """

    def _padding_segment() -> bytes:
        return b"\xff\xe1" + (65533).to_bytes(2, "big") + b"\x00" * 65533

    sof = (
        b"\xff\xc0"
        + (17).to_bytes(2, "big")
        + b"\x08"
        + (240).to_bytes(2, "big")
        + (20).to_bytes(2, "big")
        + b"\x03\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    payload = b"\xff\xd8" + _padding_segment() * 16 + sof + b"\xff\xd9"
    assert len(payload) > 1024 * 1024  # SOF 确实落在首块之外

    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    response = _Response(payload, "image/jpeg", None)
    ydl = _AssetYoutubeDL(response)

    path, _ = _download_xhs(
        engine,
        ydl,
        tmp_path,
        MediaType.IMAGE,
        verify_declared_dimensions=True,
    )

    assert path.read_bytes() == payload


def test_image_dimensions_missing_after_deep_scan_still_fails(tmp_path):
    """深度扫描仍找不到尺寸时保持 fail-closed。"""

    payload = b"\xff\xd8" + b"\x00" * (1024 * 1024 + 64)  # 无任何段标记
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    response = _Response(payload, "image/jpeg", None)
    ydl = _AssetYoutubeDL(response)

    with pytest.raises(MediaDownloadError, match="could not be verified"):
        _download_xhs(
            engine,
            ydl,
            tmp_path,
            MediaType.IMAGE,
            verify_declared_dimensions=True,
        )
