# -*- coding: utf-8 -*-
"""Data definitions for Platform Matrix and API Endpoints."""

PLATFORM_MATRIX = [
    {
        "platform": "抖音 (Douyin)",
        "urls": "https://v.douyin.com/... (短链)\nhttps://www.douyin.com/video/<ID>\nhttps://www.douyin.com/note/<ID>\nhttps://www.iesdouyin.com/share/video/<ID>\nhttps://www.douyin.com/user/<sec_uid>?modal_id=<ID>",
        "media_types": "1. 普通横/竖屏短视频 (MP4)\n2. 普通多图文笔记 (WebP/JPEG)\n3. 实况照片 Live Photo (静态图+动态MP4成对)\n4. 作者全量主页批量作品发现",
        "quality_rule": "author-feed direct rendition 与 amemv default 原始档双源并行比对；调用 FFprobe 读取真实像素、码率与编码择优；交错探测 line=0/line=1 官方路由；分片 MP4 时长复核防误判；声明高画质档位不可被低画质覆盖；原图不压缩。",
        "crypto_remux": "无需重编码；若音视频分离由 FFmpeg remux；实况相片动态流与静态原图成对同步；完整下载后校验前 256KB SHA-256 及实际时长容差。",
        "storage_rule": "目录：downloads/抖音/<作者昵称>/\n文件名：YYYY-MM-DD-<标题> [<作品ID>].mp4\n图文独立作品夹：downloads/抖音/<作者>/<日期-标题 [ID]>/001.webp\n重名避让：自动追加 (2), (3)；同哈希文件智能复用。",
        "cookie_policy": "单个公开作品默认隔离会话 (anonymous_douyin=True)，免读取 Cookie；可选读取 Chrome Cookie（需关闭 Chrome 并指定 Profile）；遇登录或验证码明确分类提示。",
        "security_rule": "严格核对双层 awemeId，绝不拿 groupId 替代作品 ID；核对作者 sec_uid、作品 ID、媒体 ID 一致性；媒体直链限定抖音/字节官方 CDN 域名白名单；拦截非标端口与伪造域名；跨源跳转移除敏感头。",
        "risk_handling": "捕获 429 频控、登录拦截、人机验证码；SecSDK 签名失败自动回退 Playwright；固定分类诊断码传播，脱敏错误信息。",
        "maturity": "生产就绪 (已完成真实样本验证)"
    },
    {
        "platform": "抖音精选 (Selected Douyin)",
        "urls": "https://jingxuan.douyin.com/\nhttps://jingxuan.douyin.com/m/video/<ID>",
        "media_types": "1. 抖音精选长/短视频 (MP4)\n2. 抖音精选图文作品 (WebP/JPEG)",
        "quality_rule": "底层复用抖音核心媒体流探测管线，提取平台当前可提供的最高规格媒体流；FFprobe 校验实际流数据。",
        "crypto_remux": "无损封包合并，不重编码转码。",
        "storage_rule": "统一归入 downloads/抖音/<作者昵称>/ 目录下，保持命名与重名保护机制一致。",
        "cookie_policy": "默认免登录隔离会话；可选 Chrome Cookie。",
        "security_rule": "移动端路径 /m/video/ID 归一化为规范作品 ID；防串号与防投毒白名单机制同抖音。",
        "risk_handling": "同抖音，提供标准化的 SiteIssueCode 故障分类提示。",
        "maturity": "生产就绪 (已完成真实样本验证)"
    },
    {
        "platform": "小红书 (Xiaohongshu)",
        "urls": "https://xhslink.com/... 或 https://xhslink.cn/... (短链)\nhttps://www.xiaohongshu.com/explore/<Note_ID>\nhttps://www.xiaohongshu.com/discovery/item/<Note_ID>\nhttps://www.xiaohongshu.com/user/profile/<User_ID> (主页)",
        "media_types": "1. 图文笔记 (高清 JPG/PNG/WebP/HEIC)\n2. 视频笔记 (原视频 MP4)\n3. 实况相片 Live Photo (成对 HEIC 原图 + 动态 MP4)\n4. 作者主页全量作品滚动捕获",
        "quality_rule": "图文优先重构 direct CDN 原始大图直链，校验实际像素，DFT/PRV 多级优雅回退；视频优先提取 originVideoKey，比对 media.stream 与 mediaV2 选最高码率与像素；严禁封面冒充视频；动态相片自身宽高独立比对。",
        "crypto_remux": "保存原始文件 magic bytes，不转码；实况相片通过 FFmpeg 完整解码校验，成对同步命名保存；遇重名同步追加 (2)。",
        "storage_rule": "目录：downloads/小红书/<作者昵称>/<日期-标题 [ID]>/\n图片短序号：001.webp / 001.jpg；实况成对：001.heic 与 001.mp4\n重名文件：自动递增 (2) 避让，保留已有原文件。",
        "cookie_policy": "公开作品免登录；可选读取本机 Chrome 中的小红书 Cookie 应对风控；支持指定 Profile。",
        "security_rule": "官方短链执行受限单跳重定向解析，强制升级 HTTPS；严禁读取纯 HTTP；逐跳核验 xhscdn.com 等可信域名与 443 标准端口；内部作品 ID 必须与原始 Note ID 严格一致。",
        "risk_handling": "精准识别官方 /404/、sec 滑块验证与“暂不可浏览”状态；状态置为 NEEDS_AUTH，支持界面点击一键唤起 Chrome 对应笔记页解决验证后重试。",
        "maturity": "生产就绪 (已完成真实样本验证)"
    },
    {
        "platform": "快手 (Kuaishou)",
        "urls": "https://v.kuaishou.com/<ShortCode> (分享短链)\nhttps://www.kuaishou.com/short-video/<ID>\nhttps://www.kuaishou.com/profile/<User_ID> (作者主页)\nhttps://m.kuaishou.com/...、https://c.kuaishou.com/...\nhttps://v.m.chenzhongtech.com/... (移动动态网关)",
        "media_types": "1. 普通短视频 (高清 MP4)\n2. 横版图集 HORIZONTAL_ATLAS (多图 WebP)\n3. 竖版超长图 VERTICAL_ATLAS (16切片原始大图)\n4. 作者主页有界分页发现 (上限500页/1万条)",
        "quality_rule": "通过 Playwright 观察正常浏览器会话响应，提取平台提供能核验的最高质量视频流及图片尺寸；视频通过 FFprobe 核对有效流。",
        "crypto_remux": "不修改画面编码；图集经 FFmpeg 全量解码验证；长图切片如实按序号独立落盘，不强行做容易失真的拼接。",
        "storage_rule": "目录：downloads/快手/<作者昵称>/\n视频：YYYY-MM-DD-<标题> [<作品ID>].mp4\n图集：downloads/快手/<作者>/<日期-标题 [ID]>/001.webp\n切片长图：001.webp ~ 016.webp，界面清晰明示切片说明。",
        "cookie_policy": "普通公开短链与单视频免登录；作者主页翻页遇限制时提示用户开启 Chrome Cookie 或在 Chrome 完成验证。",
        "security_rule": "仅放行 kwaicdn.com, yximgs.com 等快手可信媒体 CDN；严格校验作者作品绑定身份；单次响应限制 16MB；未取得末页证据标记 PROFILE_INCOMPLETE。",
        "risk_handling": "遇到 429 频控执行 3 次指数退避重试 (5s-20s)；可恢复的中断保留已有已确认文件，支持增量补扫。",
        "maturity": "生产就绪 (已完成横版图集/单视频验证)"
    },
    {
        "platform": "哔哩哔哩 (Bilibili)",
        "urls": "https://www.bilibili.com/video/BV... (单视频 / 多P视频)\nhttps://b23.tv/... (短链)\nhttps://www.bilibili.com/opus/<ID> (动态图文)\nhttps://www.bilibili.com/read/cv<ID> (专栏文章)\nhttps://space.bilibili.com/.../lists/... (合集/系列)\nhttps://space.bilibili.com/<UID> (空间主页)",
        "media_types": "1. 单视频 / 多P视频选集 (?p=1,2) (MP4/MKV)\n2. 系列合集、电视剧、番剧、收藏播单\n3. 动态图文动态 (Opus)\n4. 专栏长文章 (CV 专栏正文与配图)",
        "quality_rule": "yt-dlp 驱动探测 bestvideo*+bestaudio/best，支持 1080p, 4K, 8K 原始音视频流；专栏图文提取平台超高分辨率原始画质配图。",
        "crypto_remux": "由本地 FFmpeg 执行音视频流合并封包 (Remuxing，-c copy)，无二次有损转码；图文图片逐张进行白名单校验。",
        "storage_rule": "目录：downloads/B站/<UP主昵称>/\n文件名：YYYY-MM-DD-<标题> [BV号].mp4\n多P文件按分P标题命名；图文作品独立作品夹，文章正文保存为 text_content.txt 并提取全部配图。",
        "cookie_policy": "公开可用画质免登录；1080p高码率、4K、大会员专享内容需在设置中开启「使用 Chrome 登录态」并在 Chrome 登录对应账号。",
        "security_rule": "媒体直链严格限定 hdslb.com, biliimg.com, bilivideo.com 官方资产白名单；拦截伪造相似域名与非标端口；专栏逐跳跳转安全追踪。",
        "risk_handling": "yt-dlp 自动重试与错误归纳；图文网络异常重试 6 次；错误归入 SiteIssueCode 标准体系。",
        "maturity": "生产就绪 (已完成单P/多P/合集/专栏实测)"
    },
    {
        "platform": "YouTube",
        "urls": "https://www.youtube.com/watch?v=<ID>\nhttps://youtu.be/<ID> (短链)\nhttps://www.youtube.com/shorts/<ID> (Shorts 短视频)\nhttps://www.youtube.com/playlist?list=<ID> (播放列表)\nhttps://www.youtube.com/@<Channel> (频道主页)",
        "media_types": "1. 普通长视频 (MP4/MKV/WebM)\n2. Shorts 竖屏短视频\n3. 播放列表 Playlists 批量下载\n4. 频道所有发布视频全量发现",
        "quality_rule": "bestvideo*+bestaudio/best 自动择优，支持 1080p, 1440p(2K), 2160p(4K), 4320p(8K)；内置 Deno runtime 应对平台 JS 算法挑战。",
        "crypto_remux": "FFmpeg 无损合并音视频流封包；保留实际流格式；支持自动提取视频封面。",
        "storage_rule": "目录：downloads/YouTube/<频道名称>/\n文件名：YYYY-MM-DD-<视频标题> [<视频ID>].mp4\n重名文件自动加 (2) 避让。",
        "cookie_policy": "公开视频免登录；年龄限制或私享内容可通过 Chrome Cookie 读取账号凭据。",
        "security_rule": "仅放行 googlevideo.com 等 YouTube 官方媒体直链；拦截非法协议与凭证泄露。",
        "risk_handling": "自动处理网络波动与分块重传；超限自动退避；标准 SiteIssueCode 映射。",
        "maturity": "生产就绪 (yt-dlp官方稳定通道)"
    },
    {
        "platform": "微信视频号 (WeChat Channels)",
        "urls": "https://weixin.qq.com/sph/<ShareCode> (分享短链)\nhttps://channels.weixin.qq.com/finder-preview/pages/sph?id=... (落地页)",
        "media_types": "1. 视频号单个视频作品 (无水印高清 MP4)\n注：图集、直播回放等官方接口不返回媒体流的非视频作品暂不支持，界面明确告知。",
        "quality_rule": "通过腾讯元宝换取合法播放直链，提取 videoInfo, h264VideoInfo, h265VideoInfo 原始最高画质流；下载后 FFprobe 复核视频流与时长。",
        "crypto_remux": "视频二进制直链前 128KB 采用 ISAAC64 算法流密码异或解密为标准合规 MP4；无需二次重编码；无 Content-Length 时按块流式计算已读字节与瞬时网速。",
        "storage_rule": "目录：downloads/视频号/<作者昵称>/\n文件名：YYYY-MM-DD-<描述> [<作品ID>].mp4\n暂存文件包含独立 job_id 隔离（如 .{job_id}.checking.mp4）；目标已存在拒绝覆盖并提醒。",
        "cookie_policy": "必需腾讯元宝登录态（因微信官方纯匿名访问不返回音视频流）：\n方式1：设置中点击「扫码登录元宝」由程序内置浏览器扫码自动加密保存；\n方式2：Chrome 加载配套扩展一键复制元宝 Cookie 粘贴保存；\n凭据通过 Windows DPAPI 本机强加密。",
        "security_rule": "严格比对元宝接口返回的 shortUri 与用户提交的分享码一致性；必须取得明确的 objectId/id，否则立即停止；仅放行 finder.video.qq.com 官方直链；重定向超 6 次拒绝；内存与日志绝不打印 Cookie 明文。",
        "risk_handling": "Cookie 失效或缺少播放权限提示用户更新登录态；网络中断自动清理未完成暂存；统一接入主队列与 pause/resume 机制。",
        "maturity": "生产就绪 (需配置元宝登录态)"
    }
]

API_ENDPOINTS = [
    # 任务相关 (12)
    {
        "id": "API-001",
        "method": "POST",
        "path": "/api/jobs",
        "name": "创建下载任务",
        "params": "JSON: { url: str (8-4096), auto_start: bool = True, force: bool = False, item_ids: list[str] | None }",
        "response": "201 Created: DownloadJob (public redacted) 包含任务ID、状态、作者、子条目列表",
        "security": "local_mutations_only 中间件 (防跨站 CSRF)；规范化 URL 查重校验",
        "errors": "400 参数格式非法; 409 任务已存在 (非 force); 422 不支持的 URL 平台"
    },
    {
        "id": "API-002",
        "method": "GET",
        "path": "/api/jobs",
        "name": "获取所有任务列表",
        "params": "None",
        "response": "200 OK: list[DownloadJob] (按创建时间逆序，所有敏感 Cookie/Token 已脱敏)",
        "security": "只读接口；_public_job 脱敏过滤",
        "errors": "无"
    },
    {
        "id": "API-003",
        "method": "GET",
        "path": "/api/jobs/{job_id}",
        "name": "获取单个任务详情",
        "params": "Path: job_id (str)",
        "response": "200 OK: DownloadJob (含所有 items 明细、output_paths、进度、错误原因)",
        "security": "只读接口；脱敏处理",
        "errors": "404 任务不存在"
    },
    {
        "id": "API-004",
        "method": "POST",
        "path": "/api/jobs/{job_id}/start",
        "name": "指定选集启动任务",
        "params": "Path: job_id; JSON: { item_ids: list[str] (min_length=1) }",
        "response": "200 OK: DownloadJob",
        "security": "local_mutations_only；校验 item_ids 合法性与范围",
        "errors": "400 未勾选任何条目; 404 任务不存在; 409 任务已在运行"
    },
    {
        "id": "API-005",
        "method": "POST",
        "path": "/api/jobs/{job_id}/pause",
        "name": "暂停正在执行的任务",
        "params": "Path: job_id (str)",
        "response": "200 OK: DownloadJob (状态变为 PAUSED)",
        "security": "local_mutations_only",
        "errors": "404 任务不存在; 409 任务不可暂停"
    },
    {
        "id": "API-006",
        "method": "POST",
        "path": "/api/jobs/{job_id}/resume",
        "name": "恢复暂停的任务继续下载",
        "params": "Path: job_id (str)",
        "response": "200 OK: DownloadJob (状态重新入队 QUEUED)",
        "security": "local_mutations_only",
        "errors": "404 任务不存在; 409 任务非暂停状态"
    },
    {
        "id": "API-007",
        "method": "POST",
        "path": "/api/jobs/{job_id}/cancel",
        "name": "取消任务执行",
        "params": "Path: job_id (str)",
        "response": "200 OK: DownloadJob (状态变为 CANCELLED)",
        "security": "local_mutations_only",
        "errors": "404 任务不存在"
    },
    {
        "id": "API-008",
        "method": "POST",
        "path": "/api/jobs/{job_id}/retry",
        "name": "重试失败项或整任务",
        "params": "Path: job_id; JSON Body: { item_id: str | None }",
        "response": "200 OK: DownloadJob",
        "security": "local_mutations_only",
        "errors": "404 任务或条目不存在; 409 任务正在运行或不可重试"
    },
    {
        "id": "API-009",
        "method": "DELETE",
        "path": "/api/jobs/{job_id}",
        "name": "删除指定任务记录",
        "params": "Path: job_id (str)",
        "response": "200 OK: { deleted: true }",
        "security": "local_mutations_only；仅删除记录不删磁盘物理文件",
        "errors": "404 任务不存在; 409 任务仍在运行中拒绝删除"
    },
    {
        "id": "API-010",
        "method": "GET",
        "path": "/api/jobs/exists",
        "name": "查重任务检索",
        "params": "Query: url (str)",
        "response": "200 OK: { exists: bool, job: { id, title, status, platform, created_at } | None }",
        "security": "只读接口；规范化源指纹比较",
        "errors": "无 (异常时返回 exists: false)"
    },
    {
        "id": "API-011",
        "method": "POST",
        "path": "/api/preview",
        "name": "解析并预览单作品分集",
        "params": "JSON: { url: str (8-4096) }",
        "response": "200 OK: { platform, author, total_items, items: [{ id, title, author, media_type, resolution, playlist_index }], warning }",
        "security": "local_mutations_only；主页链接直接快速拦截并友好提示",
        "errors": "422 主页链接直接拒绝; 422 不支持的 URL; 400 解析失败"
    },
    {
        "id": "API-012",
        "method": "GET",
        "path": "/api/jobs/{job_id}/cover",
        "name": "获取任务封面缩略图",
        "params": "Path: job_id (str)",
        "response": "200 OK: image/jpeg (优先本地视频 FFmpeg 截帧，次选远程缓存，带 24h 缓存头)",
        "security": "本地文件存在性校验；仅限图片/截帧",
        "errors": "404 任务不存在或暂无可用封面"
    },

    # 桌面与文件端点 (2)
    {
        "id": "API-013",
        "method": "POST",
        "path": "/api/folder/open",
        "name": "打开保存目录并置顶前台",
        "params": "JSON: { job_id: str | None, wechat: bool = False, prepare_only: bool = False }",
        "response": "200 OK: { status: 'opened' | 'ready', path: str, foreground: bool }",
        "security": "local_mutations_only；路径范围检查 (_resolve_within)，禁止越权跨目录打开",
        "errors": "400 目录不在安全范围内或不存在; 404 任务不存在"
    },
    {
        "id": "API-014",
        "method": "POST",
        "path": "/api/files/open",
        "name": "直接打开媒体或资源管理器高亮",
        "params": "JSON: { job_id: str, item_id: str | None, file_index: int = 0, wechat: bool = False, reveal: bool = False }",
        "response": "200 OK: { status: 'opened' | 'revealed', path: str }",
        "security": "local_mutations_only；OPENABLE_MEDIA_SUFFIXES 白名单防投毒；仅限媒体后缀",
        "errors": "400 非安全可打开格式; 404 文件不存在或已移动"
    },

    # 配置与环境端点 (5)
    {
        "id": "API-015",
        "method": "GET",
        "path": "/api/config",
        "name": "读取全局应用配置",
        "params": "None",
        "response": "200 OK: AppConfig { download_dir: str, use_chrome_cookies: bool, chrome_profile: str | None }",
        "security": "线程读锁保护",
        "errors": "无"
    },
    {
        "id": "API-016",
        "method": "PUT",
        "path": "/api/config",
        "name": "更新并保存应用配置",
        "params": "JSON: AppConfig { download_dir: str, use_chrome_cookies: bool, chrome_profile: str | None }",
        "response": "200 OK: AppConfig (更新生效后的配置)",
        "security": "local_mutations_only；目录存在性/可写性校验；chrome_profile 格式正则校验；原子写盘",
        "errors": "422 目录无法创建/不可写或 Profile 格式非法; 500 磁盘写入失败"
    },
    {
        "id": "API-017",
        "method": "GET",
        "path": "/api/environment",
        "name": "运行环境健康自检",
        "params": "None",
        "response": "200 OK: { checks: [{ id: str, ok: bool, message: str }] } (包含 ffmpeg, ffprobe, chrome, download_dir, yt_dlp, playwright)",
        "security": "只读接口；无副作用",
        "errors": "无"
    },
    {
        "id": "API-018",
        "method": "GET",
        "path": "/api/cache",
        "name": "查询分段下载缓存用量",
        "params": "None",
        "response": "200 OK: { path: str, files: int, bytes: int, busy_jobs: int, paused_jobs: int, can_clear: bool }",
        "security": "只读接口",
        "errors": "无"
    },
    {
        "id": "API-019",
        "method": "POST",
        "path": "/api/cache/clear",
        "name": "安全清理分段下载缓存",
        "params": "None",
        "response": "200 OK: { freed_bytes: int, files: int }",
        "security": "local_mutations_only；有 busy_jobs (正在下载) 时直接拦截拒清理",
        "errors": "409 存在正在下载的任务，要求先完成或取消"
    },

    # 认证与验证端点 (5)
    {
        "id": "API-020",
        "method": "POST",
        "path": "/api/jobs/{job_id}/verify",
        "name": "唤起 Chrome 打开人机验证页",
        "params": "Path: job_id (str)",
        "response": "200 OK: { status: 'opened', url: str }",
        "security": "local_mutations_only；严格校验必须为当前绑定的验证目标 URL；禁止任意 URL 打开",
        "errors": "409 任务不需要验证; 422 原始链接不可验证或不可信"
    },
    {
        "id": "API-021",
        "method": "GET",
        "path": "/api/wechat/status",
        "name": "查询视频号元宝 Cookie 状态",
        "params": "None",
        "response": "200 OK: { cookie_configured: bool, chrome_fallback: bool }",
        "security": "不返回任何 Cookie 明文数据，零凭证泄露",
        "errors": "无"
    },
    {
        "id": "API-022",
        "method": "POST",
        "path": "/api/wechat/cookie",
        "name": "保存或清除视频号元宝 Cookie",
        "params": "JSON: { cookie: str (max_length=32768) }",
        "response": "200 OK: { saved: true }",
        "security": "local_mutations_only；禁止换行注入；Windows DPAPI 密文加密保存",
        "errors": "422 包含换行符非法请求头"
    },
    {
        "id": "API-023",
        "method": "POST",
        "path": "/api/wechat/login",
        "name": "触发 Playwright 扫码登录元宝",
        "params": "None",
        "response": "200 OK: { saved: true }",
        "security": "local_mutations_only；全局互斥锁防止并发重复弹窗；凭证内存脱敏",
        "errors": "400 扫码超时或缺少依赖; 409 已有一个扫码窗口在运行中"
    },
    {
        "id": "API-024",
        "method": "POST",
        "path": "/api/wechat/jobs",
        "name": "旧视频号任务创建入口 (停用拦截)",
        "params": "JSON: { url: str }",
        "response": "410 Gone: { detail: '旧视频号任务入口已停用。请在首页粘贴链接创建任务...' }",
        "security": "统一所有平台到 DownloadManager 调度队列",
        "errors": "410 Gone (恒定返回)"
    },

    # 旧视频号兼容端点 (3)
    {
        "id": "API-025",
        "method": "GET",
        "path": "/api/wechat/jobs",
        "name": "查询历史旧视频号任务记录",
        "params": "None",
        "response": "200 OK: list[dict] (逆序返回 data/wechat-jobs.json 中的旧任务)",
        "security": "只读接口；脱敏处理",
        "errors": "无"
    },
    {
        "id": "API-026",
        "method": "DELETE",
        "path": "/api/wechat/jobs/{job_id}",
        "name": "删除历史旧视频号任务记录",
        "params": "Path: job_id (str)",
        "response": "200 OK: { deleted: true }",
        "security": "local_mutations_only；运行中拦截",
        "errors": "404 任务不存在; 409 任务仍在运行中"
    },
    {
        "id": "API-027",
        "method": "POST",
        "path": "/api/wechat/jobs/{job_id}/cancel",
        "name": "取消历史旧视频号任务",
        "params": "Path: job_id (str)",
        "response": "200 OK: dict (更新为 cancelling 状态)",
        "security": "local_mutations_only",
        "errors": "404 任务不存在"
    },

    # 系统运行时与事件通信 (3)
    {
        "id": "API-028",
        "method": "GET",
        "path": "/api/health",
        "name": "服务健康状态与构建指纹自检",
        "params": "None",
        "response": "200 OK: { status: 'ok', app_id, version, build_id, source_build_id, restart_required, instance_id, server_pid, server_port, project_root, recovery }",
        "security": "只读诊断接口",
        "errors": "无"
    },
    {
        "id": "API-029",
        "method": "POST",
        "path": "/api/runtime/stop",
        "name": "基于安全令牌的受控停机",
        "params": "Header: X-Shiying-Stop-Token (必填且与启动密钥一致)",
        "response": "200 OK: { status: 'stopping', instance_id: str }",
        "security": "限制客户端仅限 127.0.0.1/::1；必须通过恒定时间 secrets.compare_digest 校验令牌",
        "errors": "403 跨机访问或停止令牌错误; 503 本实例未携带停止令牌启动"
    },
    {
        "id": "API-030",
        "method": "GET",
        "path": "/api/events",
        "name": "Server-Sent Events 任务实时事件长连接",
        "params": "None",
        "response": "200 OK: text/event-stream (推送 event: job, data: JSON 任务更新；: keepalive 心跳)",
        "security": "客户端断开自动注销广播监听器；禁用代理反向缓存",
        "errors": "无"
    },

    # 中间件与页面基础设施 (3)
    {
        "id": "API-031",
        "method": "Middleware",
        "path": "TrustedHostMiddleware",
        "name": "HTTP Host 头部主机白名单拦截",
        "desc": "基于 Starlette TrustedHostMiddleware，仅放行 Host 为 127.0.0.1 及 localhost 的请求，拦截非法 DNS 绑定与代理伪造请求。",
        "params": "全局作用于所有进入 FastAPI 的请求",
        "response": "合法放行；非法返回 400 Invalid host header",
        "security": "防公网暴露与 DNS 重绑定攻击",
        "errors": "400 Invalid Host"
    },
    {
        "id": "API-032",
        "method": "Middleware",
        "path": "local_mutations_only",
        "name": "全局跨站请求伪造 (CSRF) 防御拦截",
        "desc": "对非幂等请求（POST, PUT, DELETE），核查 Sec-Fetch-Site 是否为 cross-site 或 Origin 是否与本机 base_url 匹配，拒绝外站触发本机写操作。",
        "trigger": "全局拦截所有 HTTP 写操作",
        "response": "合法放行；非法返回 403: { detail: '只接受本机拾影页面的操作。' }",
        "security": "杜绝恶意网页在后台通过 fetch 偷发下载或修改设置",
        "errors": "403 Forbidden"
    },
    {
        "id": "API-033",
        "method": "Middleware",
        "path": "disable_runtime_asset_cache",
        "name": "主页与静态资源防陈旧缓存中间件",
        "desc": "对 / 及 /static/ 路径强制覆盖 Cache-Control: no-store, max-age=0 及 Pragma: no-cache，杜绝客户端由于浏览器磁盘缓存执行旧版本代码导致功能失效。",
        "trigger": "请求静态资源与 index.html 时自动附加头部",
        "response": "注入响应头后放行",
        "security": "保障多版本平滑升级与即时生效",
        "errors": "无"
    }
]

print(f"Platform matrix: {len(PLATFORM_MATRIX)} platforms.")
print(f"API endpoints: {len(API_ENDPOINTS)} endpoints.")
