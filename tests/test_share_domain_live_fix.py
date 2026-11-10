from types import SimpleNamespace
import pytest
from app.downloader import MediaDownloader, DownloaderConfig
from app.errors import DiscoveryError
from app.kuaishou import is_page_url, source_identity
from app.models import Platform, SourceKind
from app.platforms import identify_url, UnsupportedUrlError


@pytest.mark.parametrize('host', ['xhslink.cn', 'xhslink.com'])
def test_xhs_redirect_is_bound_to_xiaohongshu(monkeypatch, host):
    from app import downloader
    class Response:
        status_code = 302
        headers = {'Location': 'https://www.xiaohongshu.com/discovery/item/6ae9fd38000000001402ab93?xsec_token=abc'}
        def close(self): pass
    calls = []
    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setattr(downloader.requests, 'get', get)
    url = f'https://{host}/o/AH3CnLCUzyN'
    assert identify_url(url).platform == Platform.XIAOHONGSHU
    engine = MediaDownloader(DownloaderConfig(cookie_browser=None))
    target, used_cookies = engine._resolve_short_url(url)
    assert identify_url(target).kind == SourceKind.ITEM
    assert 'xsec_token=abc' in target and not used_cookies
    assert calls[0][1]['allow_redirects'] is False
    engine._resolve_short_url(f'http://{host}/o/AH3CnLCUzyN')
    assert calls[-1][0].startswith('https://')
    Response.headers = {'Location': 'https://example.com/private'}
    with pytest.raises(DiscoveryError):
        engine._resolve_short_url(url)


def test_xhs_unavailable_note_gives_actionable_error(monkeypatch):
    from app import xiaohongshu
    def rejected(*args, **kwargs):
        raise xiaohongshu._XiaohongshuRedirectRejected(
            'untrusted-target', target_url='https://www.xiaohongshu.com/404/sec_example?error_code=300031')
    monkeypatch.setattr(xiaohongshu, '_open_xiaohongshu_response', rejected)
    with pytest.raises(DiscoveryError, match='重新复制完整分享链接'):
        xiaohongshu._read_page(None, 'https://www.xiaohongshu.com/explore/6411cf99000000001300b6d9')


def test_kuaishou_mobile_gateway_is_narrowly_trusted():
    url = 'https://v.m.chenzhongtech.com/fw/photo/3xur3d72hamxxea'
    assert is_page_url(url)
    assert source_identity(url) == ('item', '3xur3d72hamxxea')
    assert not is_page_url('https://evil.v.m.chenzhongtech.com/fw/photo/3xur3d72hamxxea')
    assert source_identity('https://kphk9mez.m.chenzhongtech.com/fw/photo/3x2qxvfmeenfj3m') == ('item', '3x2qxvfmeenfj3m')
    assert not is_page_url('https://kphk9mez.m.chenzhongtech.com/other/path')
    assert not is_page_url('https://kphk9mez.m.chenzhongtech.com.evil.test/fw/photo/3x2qxvfmeenfj3m')


@pytest.mark.parametrize('host', ['m.kuaishou.com', 'c.kuaishou.com', 'v.m.chenzhongtech.com'])
def test_kuaishou_direct_mobile_link(host):
    result = identify_url(f'https://{host}/fw/photo/3xd8aqf7evqbxbc')
    assert result.platform == Platform.KUAISHOU and result.kind == SourceKind.ITEM


def test_jingxuan_mobile_video_url():
    result = identify_url('https://jingxuan.douyin.com/m/video/7662178203703852011')
    assert result.platform == Platform.DOUYIN
    assert result.url == 'https://www.douyin.com/video/7662178203703852011'


@pytest.mark.parametrize('host, kind', [
    ('www.iesdouyin.com', 'slides'), ('www.iesdouyin.com', 'note'),
    ('iesdouyin.com', 'video'), ('m.douyin.com', 'video'),
])
def test_douyin_mobile_share_normalizes_to_same_item(host, kind):
    result = identify_url(f'https://{host}/share/{kind}/7668561203254657707/?region=CN')
    assert result.platform == Platform.DOUYIN and result.kind == SourceKind.ITEM
    assert result.url == 'https://www.douyin.com/video/7668561203254657707'


@pytest.mark.parametrize('url', [
    'https://space.bilibili.com/2142762/lists/3662502?type=season',
    'https://space.bilibili.com/1958703906/channel/seriesdetail?sid=547718',
    'https://space.bilibili.com/492744983/favlist?fid=933034683',
    'https://www.bilibili.com/list/ml2638432613',
])
def test_bilibili_collections_are_not_classified_as_profiles(url):
    result = identify_url(url)
    assert result.platform == Platform.BILIBILI and result.kind == SourceKind.PLAYLIST


@pytest.mark.parametrize('url', [
    'https://www.bilibili.com/read/cv26233488',
    'https://www.bilibili.com/opus/1086088287848693764',
])
def test_bilibili_articles_and_opus_are_identified(url):
    result = identify_url(url)
    assert result.platform == Platform.BILIBILI
    assert result.kind == SourceKind.ITEM

def test_mobile_page_video_binds_media_to_requested_work():
    from app.kuaishou import mobile_page_video
    media_id = '3xur3d72hamxxea'
    url = 'https://v.kwimgs.com/video.mp4?clientCacheKey=' + media_id + '_b.mp4'
    state = {'one': {'photo': {'userEid': '3xuser', 'userName': 'Author', 'duration': 7000,
        'mainMvUrls': [{'url': url}], 'manifest': {'adaptationSet': [{'representation': [{'url': url, 'width': 1280, 'height': 720}]}]}}}}
    video = mobile_page_video(state, media_id)
    assert video and video.media_id == media_id and video.assets
    assert mobile_page_video(state, '3xdifferent') is None
    assert mobile_page_video({**state, 'two': state['one']}, media_id) is None

def test_kuaishou_vertical_atlas_uses_ordered_verified_segments():
    import base64
    from app.kuaishou import mobile_page_atlas
    prefix = base64.urlsafe_b64encode(b'123456_987654').decode().rstrip('=')
    photo = {'photoType': 'VERTICAL_ATLAS', 'photoId': '123456', 'userEid': '3xowner', 'userName': 'Author',
             'ext_params': {'atlas': {'cdn': ['p2.a.yximgs.com'],
                 'list': [f'/ufile/atlas/{prefix}_{i}.webp' for i in range(2)],
                 'size': [{'w': 100, 'h': 200}, {'w': 100, 'h': 40}]}}}
    result = mobile_page_atlas({'entry': {'photo': photo}}, '3xphoto', 'VERTICAL_ATLAS')
    assert result and result.media_type == 'image' and len(result.assets) == 2
    assert [x.index for x in result.assets] == [1, 2]
    photo['ext_params']['atlas']['cdn'] = ['evil.test']
    assert mobile_page_atlas({'entry': {'photo': photo}}, '3xphoto', 'VERTICAL_ATLAS') is None
    photo['ext_params']['atlas']['cdn'] = ['p2.a.yximgs.com']
    photo['photoId'] = '999999'
    assert mobile_page_atlas({'entry': {'photo': photo}}, '3xphoto', 'VERTICAL_ATLAS') is None


def test_kuaishou_horizontal_atlas_preserves_all_declared_images():
    import base64
    from app.kuaishou import mobile_page_atlas
    prefix = base64.urlsafe_b64encode(b'123456_987654').decode().rstrip('=')
    photo = {'photoType': 'HORIZONTAL_ATLAS', 'photoId': '123456', 'userEid': '3xowner',
             'ext_params': {'atlas': {'cdn': ['p2.a.yximgs.com'],
                 'list': [f'/ufile/atlas/{prefix}_{i}.webp' for i in range(3)],
                 'size': [{'w': 100, 'h': 200}] * 3}}}
    result = mobile_page_atlas({'entry': {'photo': photo}}, '3xphoto', 'HORIZONTAL_ATLAS')
    assert result and [asset.index for asset in result.assets] == [1, 2, 3]
    photo['ext_params']['atlas']['size'].pop()
    assert mobile_page_atlas({'entry': {'photo': photo}}, '3xphoto', 'HORIZONTAL_ATLAS') is None
