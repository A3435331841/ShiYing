from __future__ import annotations

import pytest

from app.errors import (
    SiteIssueCode,
    TemporaryAccessError,
    classify_site_issue,
    is_explicit_rate_limit_message,
)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP Error 429: Too Many Requests", SiteIssueCode.RATE_LIMITED),
        (
            "Douyin request failed. Reason category: http-429",
            SiteIssueCode.RATE_LIMITED,
        ),
        (
            "Douyin API response was incomplete. Reason category: api-response-missing",
            SiteIssueCode.SITE_RESPONSE_CHANGED,
        ),
        ("This request requires a CAPTCHA", SiteIssueCode.VERIFICATION_REQUIRED),
        ("Login required to continue", SiteIssueCode.LOGIN_REQUIRED),
        (
            "A CAPTCHA is not required for this temporary response",
            SiteIssueCode.UNKNOWN,
        ),
        (
            "Media endpoint returned HTTP 404 after redirect",
            SiteIssueCode.MEDIA_LINK_EXPIRED,
        ),
        (
            "Media endpoint returned HTTP 401 after redirect",
            SiteIssueCode.MEDIA_LINK_EXPIRED,
        ),
        (
            "Media endpoint returned an empty response",
            SiteIssueCode.MEDIA_LINK_EXPIRED,
        ),
        ("HTTP Error 404: source page missing", SiteIssueCode.CONTENT_UNAVAILABLE),
        ("This video is private", SiteIssueCode.CONTENT_UNAVAILABLE),
        ("Video unavailable", SiteIssueCode.CONTENT_UNAVAILABLE),
        ("This post has been deleted", SiteIssueCode.CONTENT_UNAVAILABLE),
        ("Your IP is blocked by the website", SiteIssueCode.REQUEST_REJECTED),
        ("This video is still processing", SiteIssueCode.SITE_PROCESSING),
        ("HTTP Error 403: Forbidden", SiteIssueCode.REQUEST_REJECTED),
        ("Media endpoint returned HTTP 425", SiteIssueCode.REQUEST_REJECTED),
        ("网络环境存在风险，请稍后重试", SiteIssueCode.REQUEST_REJECTED),
        ("HTTP Error 503: Service Unavailable", SiteIssueCode.SITE_UNAVAILABLE),
        ("Network request failed: connection reset", SiteIssueCode.NETWORK_ERROR),
        ("Media endpoint returned HTTP 408", SiteIssueCode.NETWORK_ERROR),
        ("Secure media connection failed", SiteIssueCode.NETWORK_ERROR),
        (
            "A local DNS or web filter blocked Douyin before the site loaded",
            SiteIssueCode.NETWORK_ERROR,
        ),
        ("Website Filtered by blocked.dnsfilter.com", SiteIssueCode.NETWORK_ERROR),
        (
            "Chrome cookies could not be read from the cookie database",
            SiteIssueCode.COOKIE_UNAVAILABLE,
        ),
        (
            "Douyin automatic item refresh was skipped because Chrome Cookie "
            "is disabled for this task",
            SiteIssueCode.COOKIE_UNAVAILABLE,
        ),
        (
            "This video is not available in your country",
            SiteIssueCode.REGION_RESTRICTED,
        ),
        (
            "Media endpoint redirected outside the trusted Douyin hosts",
            SiteIssueCode.SECURITY_BLOCKED,
        ),
        (
            "Media TLS certificate validation failed",
            SiteIssueCode.SECURITY_BLOCKED,
        ),
        (
            "Media content changed between the range probe and local probe",
            SiteIssueCode.SITE_RESPONSE_CHANGED,
        ),
        ("ffmpeg was not found", SiteIssueCode.LOCAL_CONFIGURATION),
        (
            "腾讯未返回播放信息。图集、直播回放等非视频作品暂不支持，普通视频请更新腾讯元宝 Cookie 后重试。",
            SiteIssueCode.COOKIE_UNAVAILABLE,
        ),
        (
            "播放凭证不完整，请更新腾讯元宝登录态后重试。",
            SiteIssueCode.COOKIE_UNAVAILABLE,
        ),
        (
            "微信未允许播放该作品，请检查链接和账号观看权限。",
            SiteIssueCode.CONTENT_UNAVAILABLE,
        ),
        (
            "网络超时断开",
            SiteIssueCode.NETWORK_ERROR,
        ),
        (
            "请求超时",
            SiteIssueCode.NETWORK_ERROR,
        ),
        (
            "小红书暂时无法打开这篇笔记。链接可能已过期、作品不可见或访问受限；请从小红书重新复制完整分享链接后重试。",
            SiteIssueCode.CONTENT_UNAVAILABLE,
        ),
        (
            "Xiaohongshu note redirect was blocked before requesting an "
            "untrusted or mismatched target",
            SiteIssueCode.SECURITY_BLOCKED,
        ),
        (
            "Kuaishou media redirect was blocked before requesting an untrusted target",
            SiteIssueCode.SECURITY_BLOCKED,
        ),
        (
            "Unsupported Kuaishou video, profile, or share URL",
            SiteIssueCode.SITE_RESPONSE_CHANGED,
        ),
        (
            "无法解析小红书分享链接，请检查网络并重新复制链接。",
            SiteIssueCode.NETWORK_ERROR,
        ),
    ],
)
def test_classify_site_issue_corpus(
    message: str,
    expected: SiteIssueCode,
) -> None:
    assert classify_site_issue(message) == expected


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            "Douyin displayed an explicit CAPTCHA challenge",
            SiteIssueCode.VERIFICATION_REQUIRED,
        ),
        (
            "Douyin requires a current Chrome login session",
            SiteIssueCode.LOGIN_REQUIRED,
        ),
        (
            "Authentication is required but the response gave no details",
            SiteIssueCode.LOGIN_REQUIRED,
        ),
    ],
)
def test_classify_site_issue_uses_authentication_context(
    message: str,
    expected: SiteIssueCode,
) -> None:
    assert classify_site_issue(message, authentication_required=True) == expected


def test_classify_site_issue_preserves_explicit_code_for_opaque_error() -> None:
    error = TemporaryAccessError(
        "opaque remote failure",
        issue_code=SiteIssueCode.SECURITY_BLOCKED,
    )

    assert classify_site_issue(error) == SiteIssueCode.SECURITY_BLOCKED


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("HTTP Error 429: Too Many Requests", True),
        ("当前访问频繁，请稍后再试", True),
        ("操作太频繁", True),
        ("网络环境存在风险，请稍后再试", False),
        ("接口异常，请稍后再试", False),
        ("HTTP Error 403: Forbidden", False),
    ],
)
def test_explicit_rate_limit_detection_is_narrow(
    message: str,
    expected: bool,
) -> None:
    assert is_explicit_rate_limit_message(message) is expected


def test_auth_context_does_not_turn_negated_captcha_text_into_verification() -> None:
    message = "A CAPTCHA is not required; refresh the current login session"

    assert (
        classify_site_issue(message, authentication_required=True)
        == SiteIssueCode.LOGIN_REQUIRED
    )
