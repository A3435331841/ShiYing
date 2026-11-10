# -*- coding: utf-8 -*-
"""Full taxonomy and decision tree for SiteIssueCode in Shiying."""

SITE_ISSUE_TREE = [
    {
        "code": "rate_limited",
        "name": "访问限频 / 速率受限 (HTTP 429)",
        "condition": "请求频率过高，触发对端 CDN 或网关防火墙的限频阈值",
        "patterns": "too many requests, rate-limit, 访问频繁, 请求过于频繁, 频繁操作, HTTP 429, status code 429",
        "platforms": "所有平台 (尤其抖音主页、小红书翻页、快手接口)",
        "internal_action": "捕获 429；停止当前批次后续翻页；启动指数退避重试 (1s -> 2s -> 4s)；超时后标记任务为 FAILED",
        "ui_presentation": "卡片显示橙色/红色徽标，文字提示'访问过于频繁，平台已实施临时限频'",
        "user_guidance": "1. 暂停高频并发解析；\n2. 等待 3-5 分钟后点击卡片上的【重试】；\n3. 若下载作者主页，建议在设置中开启适当的间隔延迟。"
    },
    {
        "code": "verification_required",
        "name": "人机验证 / 滑块验证码挑战",
        "condition": "目标网站探测到自动化行为，返回滑块验证码、点选验证码或重定向至 /verify 页面",
        "patterns": "captcha required, captcha challenge, solve the captcha, verify you are human, 请完成验证码, 请拖动滑块, security verification",
        "platforms": "小红书、抖音、快手",
        "internal_action": "提取挑战 URL（如小红书 noteUrl）；停止自动化探测；将任务状态置为 NEEDS_AUTH 或 FAILED，向前端广播 issue_code",
        "ui_presentation": "卡片突出展示橙色【需要验证】徽标，提供【一键唤醒 Chrome 验证】按钮",
        "user_guidance": "1. 点击卡片上的【前往验证】按钮，拾影将自动唤起系统 Chrome 打开对应页面；\n2. 在浏览器中用鼠标完成滑块拖动或点选；\n3. 回到拾影点击【重试】，任务将自动继续。"
    },
    {
        "code": "login_required",
        "name": "需要登录 / 会话鉴权失效",
        "condition": "作品设为仅好友可见/私密，或平台强制要求登录才能查看（如 B站大会员、抖音私密视频、小红书需登录笔记）",
        "patterns": "login required, please log in, need to be logged in, you are not logged in, login expired, session expired, 请先登录, 登录后查看",
        "platforms": "B站、抖音、小红书、微信视频号",
        "internal_action": "校验当前配置中是否启用了 Chrome Cookie 或元宝 Cookie；未启用或失效时停止下载，任务置为 NEEDS_AUTH",
        "ui_presentation": "卡片展示黄色【需要登录】徽标，操作栏变为【配置登录态】快捷按钮",
        "user_guidance": "1. 在日常使用的 Chrome 中登录该平台账号；\n2. 打开拾影【设置】抽屉，勾选【使用 Chrome 浏览器的登录态】并保存；\n3. 点击卡片【重试】即可下载。"
    },
    {
        "code": "cookie_unavailable",
        "name": "本地 Cookie 读取失败 / 被占用锁定",
        "condition": "尝试从系统 Chrome 读取 Cookies SQLite 数据库时，数据库被 Chrome 进程独占锁定，或 DPAPI 解密失败",
        "patterns": "chrome cookies could not be read, cookie database, failed to load cookies, could not decrypt, 腾讯元宝 cookie, 元宝登录态失效",
        "platforms": "微信视频号 (元宝会话)、所有开启了 Chrome Cookie 的平台",
        "internal_action": "捕获 sqlite3.OperationalError 或 DPAPI 异常；不向外部暴露 Cookie 残片；任务置为 NEEDS_AUTH",
        "ui_presentation": "卡片显示【登录态读取失败】，弹出环境修复提示弹窗",
        "user_guidance": "1. 请完全退出正在运行的 Google Chrome 浏览器（检查系统托盘或任务管理器确保彻底关闭）；\n2. 视频号用户请在设置中重新点击【扫码登录】更新元宝 Cookie；\n3. 重新发起任务。"
    },
    {
        "code": "content_unavailable",
        "name": "作品不存在 / 已被作者删除或转私密",
        "condition": "源站直接返回 HTTP 404 或 410 Gone，或接口明确指明作品被删除/被封禁",
        "patterns": "http error 404, http error 410, video is unavailable, private video, has been removed, has been deleted, 不存在或当前不可见, 无权访问",
        "platforms": "所有平台",
        "internal_action": "立即终止针对该 ID 的所有重试尝试，直接标记该 DownloadItem 为 FAILED，避免无谓消耗资源",
        "ui_presentation": "卡片显示暗灰色【作品已下架】徽标，提供【删除记录】按钮",
        "user_guidance": "该视频或图文已被原作者删除，或被平台官方屏蔽/封禁。此链接已无法下载，请核对链接是否有效。"
    },
    {
        "code": "request_rejected",
        "name": "请求被拒绝 / 平台风控拦截 (HTTP 403)",
        "condition": "请求 Header 异常、缺少签名、IP 命中平台高危库或跨域策略被拒绝",
        "patterns": "http error 403, forbidden, access denied, request rejected, 风控, 网络环境存在风险, ip address is blocked",
        "platforms": "抖音、B站、YouTube、快手",
        "internal_action": "记录 403 诊断码；交替切换官方备用路由（如 Line0/Line1）；若全部拒绝则标记 FAILED",
        "ui_presentation": "卡片显示红色【请求被拒绝 (403)】",
        "user_guidance": "1. 检查本地网络或代理设置是否正常；\n2. 若使用了 VPN / 代理工具，尝试切换节点或关闭全局代理；\n3. 稍后重新提交。"
    },
    {
        "code": "site_processing",
        "name": "媒体正在处理 / 平台转码审核中",
        "condition": "作品刚刚上传发布，平台后台仍在进行高清转码、合规审核或切片索引生成",
        "patterns": "still processing, being processed, being transcoded, processing this video, under review, 正在处理, 审核中, 转码中",
        "platforms": "抖音、B站、YouTube",
        "internal_action": "标记为临时等待态；暂不判定为永久失败；延时后尝试二次探测",
        "ui_presentation": "卡片展示蓝色【平台转码中】状态徽标",
        "user_guidance": "作品刚刚发布，平台后台正在审核或转码高清版本。请等待 1-2 分钟后再试。"
    },
    {
        "code": "region_restricted",
        "name": "地域限制 / 版权地区限制 (HTTP 451)",
        "condition": "作品仅限特定国家或地区播放，当前出口 IP 所在地区不在版权许可范围内",
        "patterns": "not available in your country, not available in your region, geo-restricted, region restricted, http error 451",
        "platforms": "B站 (港澳台番剧)、YouTube",
        "internal_action": "捕获 451 异常，阻断下载重试",
        "ui_presentation": "卡片展示黄色【地区受限】徽标",
        "user_guidance": "该作品由于版权原因在当前网络所在地区不可用。需要使用对应受许可地区的网络环境访问。"
    },
    {
        "code": "site_response_changed",
        "name": "响应结构变动 / 防串号防空内容触发",
        "condition": "对端 API 返回字段变更，或返回数据与请求的目标作品 ID / 作者 ID 校验不一致（串号拦截）",
        "patterns": "different video, different author, cross-wired, identity changed, returned no note data, returned an empty file, missing aweme",
        "platforms": "抖音、快手、小红书、微信视频号",
        "internal_action": "严格防串号断言拦截！拒绝下载不匹配媒体；将现场响应写入隔离诊断日志，抛出 SITE_RESPONSE_CHANGED",
        "ui_presentation": "卡片展示红色【数据校验异常】，附带防串号拦截说明",
        "user_guidance": "系统检测到平台返回的作品数据与您提交的链接不符（触发防串号保护）。已自动为您终止下载，请确保链接准确。"
    },
    {
        "code": "media_link_expired",
        "name": "临时直链过期 / 签名 Token 失效 (HTTP 410)",
        "condition": "解析出来的媒体播放直链（CDN URL）有效期通常为 1-2 小时，超过时限后访问报 401/403/410",
        "patterns": "media endpoint returned http 410, media link expired, signature expired, url has expired, access token invalid",
        "platforms": "所有平台 (尤其是长视频或大文件合集)",
        "internal_action": "自动触发“重新解析直链”动作（Refresh Media URL），使用最新有效链接重试下载，无需用户从头创建任务",
        "ui_presentation": "短暂显示【刷新链接中】，随后自动恢复下载",
        "user_guidance": "系统已自动刷新过期临时直链，下载将继续进行。"
    },
    {
        "code": "site_unavailable",
        "name": "源站宕机 / 平台服务不可用 (HTTP 5xx)",
        "condition": "目标平台服务器故障，返回 500 Internal Server Error, 502 Bad Gateway, 503 Service Unavailable",
        "patterns": "http 500, http 502, http 503, service unavailable, server error, bad gateway",
        "platforms": "所有平台",
        "internal_action": "启动短间隔等待与重试，重试 3 次均失败后标记任务 FAILED",
        "ui_presentation": "卡片展示黄色【平台服务器异常 (5xx)】",
        "user_guidance": "目标平台服务器暂时发生故障或网络波动，请稍后再试或在浏览器中检查原网页是否正常。"
    },
    {
        "code": "network_error",
        "name": "本地网络超时 / 连接中断 / DNS 失败",
        "condition": "本地网络断开、Wi-Fi 波动、DNS 无法解析域名或 TCP 连接握手超时",
        "patterns": "network timeout, network request failed, connection failed, secure media connection failed, EOF occurred",
        "platforms": "所有平台",
        "internal_action": "保留已下载的 .part 分段临时文件，允许断点续传；标记为 FAILED 或 INTERRUPTED",
        "ui_presentation": "卡片展示红色【网络连接超时】，提供【重试】按钮",
        "user_guidance": "1. 检查本机网络连接是否通畅；\n2. 确认网络恢复后，直接点击卡片上的【重试】按钮，将从已下载进度处无缝继续。"
    },
    {
        "code": "security_blocked",
        "name": "安全策略阻断 / SSRF 与非法重定向防御",
        "condition": "媒体链接重定向至私网 IP (127.0.0.1, 192.168.x.x, 10.x.x.x) 或未知非受信外部域名、非标端口",
        "patterns": "untrusted url, untrusted douyin media url, outside the trusted, nonstandard-port, redirect could not be trusted, security check",
        "platforms": "所有平台 (引擎底层逐跳校验)",
        "internal_action": "强制切断 HTTP 连接，立即抛出 SecurityException，向用户上报安全阻断事件",
        "ui_presentation": "卡片展示深红色【安全策略已拦截】",
        "user_guidance": "该链接尝试重定向至未授权的外部地址或局域网私有地址，系统已作为潜在安全风险予以主动拦截。"
    },
    {
        "code": "local_configuration",
        "name": "本地环境配置缺失 / 目录权限不足",
        "condition": "系统缺少 FFmpeg/FFprobe 组件、下载保存目录不存在或无写入权限、磁盘空间耗尽",
        "patterns": "ffmpeg required, ffprobe required, download directory is not writable, permission denied, disk full",
        "platforms": "所有平台 (本地环境层)",
        "internal_action": "在启动阶段或任务准备阶段进行前置检查，不满足要求直接拦截任务启动",
        "ui_presentation": "界面顶部弹出醒目黄色通知栏，明确指示缺失的依赖项",
        "user_guidance": "1. 请按照提示安装 FFmpeg 与 FFprobe，并将其配置到系统 PATH；\n2. 打开【设置】抽屉，选择一个拥有充分读写权限且磁盘空间充裕的文件夹作为保存目录。"
    },
    {
        "code": "unknown",
        "name": "未分类未知异常 (保底收敛)",
        "condition": "未命中上述 14 种已知特征的其他未知异常",
        "patterns": "Exception, RuntimeError 等未分类错误栈",
        "platforms": "所有平台",
        "internal_action": "记录完整清洗后的脱敏错误日志，返回友好通用错误描述，保证后台服务稳定不崩溃",
        "ui_presentation": "卡片展示灰色【未知错误】，显示脱敏后的简明错误摘要",
        "user_guidance": "遇到未预期的未知错误。建议刷新页面重试，或查看日志以获取进一步调试信息。"
    }
]

print(f"Loaded {len(SITE_ISSUE_TREE)} SiteIssueCode taxonomy items.")
