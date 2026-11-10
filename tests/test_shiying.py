import unittest
from app.wechat import validate_link, media_info
from app.platforms import identify_url

class ShiyingTests(unittest.TestCase):
    def test_platforms(self):
        self.assertEqual(identify_url('分享 https://v.douyin.com/abc/').platform.value,'douyin')
        self.assertEqual(identify_url('https://jingxuan.douyin.com/video/12345').url,'https://www.douyin.com/video/12345')
        self.assertEqual(identify_url('https://v.kuaishou.com/abc').platform.value,'kuaishou')
        self.assertEqual(identify_url('https://xhslink.com/a/abc').platform.value,'xiaohongshu')
        self.assertEqual(identify_url('https://www.bilibili.com/video/BV1xx411c7mD').platform.value,'bilibili')
    def test_wechat_share_text(self):
        self.assertEqual(validate_link('分享 https://weixin.qq.com/sph/Aajcpfmjel。'),'https://weixin.qq.com/sph/Aajcpfmjel')
    def test_untrusted_urls_rejected(self):
        for u in ('https://weixin.qq.com.evil.example/sph/a','http://weixin.qq.com/sph/a','https://weixin.qq.com:444/sph/a','https://weixin.qq.com/other'):
            with self.assertRaises(ValueError): validate_link(u)
    def test_cover_and_encrypted_media_never_succeed(self):
        for feed in ({'coverUrl':'https://finder.video.qq.com/cover'}, {'videoInfo':{'url':'https://evil.example/test.mp4'}}):
            with self.assertRaises(ValueError): media_info(feed)
    def test_trusted_media(self):
        u='https://finder.video.qq.com/video.mp4'
        self.assertEqual(media_info({'videoInfo':{'url':u}}),u)

if __name__ == '__main__': unittest.main()
