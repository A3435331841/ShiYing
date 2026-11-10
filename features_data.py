# -*- coding: utf-8 -*-
"""Data definitions for Shiying (拾影) features matrix."""

ALL_FEATURES = [
    # -------------------------------------------------------------
    # 一、平台接入与媒体解析 (38项)
    # -------------------------------------------------------------
    {
        "id": "F-PL-001",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "单作品普通视频解析与最高流探测",
        "desc": "基于 awemeId 进行严格双层核验，同时探测 author-feed 的 direct rendition 与固定 amemv 的 default 原始档，交错尝试 line=0 与 line=1 官方路由，使用 FFprobe 实测像素、码率与编码择优，绝不采用 groupId 替代作品 ID。",
        "trigger": "首页输入框粘贴抖音单视频链接或分享文案点击「解析并下载」",
        "code": "app/douyin.py: verified_aweme_metadata(); app/engine/douyin_probe.py: _probe_candidate_media()",
        "status": "生产级就绪",
        "rules": "严格防串号；仅限受信字节/抖音视频 CDN 域名族；禁止非标准端口；支持分片 MP4 时长复核。"
    },
    {
        "id": "F-PL-002",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "单作品普通图文提取与原图直链",
        "desc": "支持抖音图文作品解析，从 React Flight SSR 数据中解析最高像素 images[].url_list，逐字节写入本地目标文件，不在本地进行二次重编码或转码，保障原始画质。",
        "trigger": "首页输入框粘贴图文链接（如 douyin.com/note/ID 或分享短链）",
        "code": "app/douyin.py: verified_aweme_metadata(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "仅限可信 CDN；无二次压缩；多图作品使用 001.webp 等短序号独立收纳到作品文件夹。"
    },
    {
        "id": "F-PL-003",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "Live Photo 实况相片成对解析与落盘",
        "desc": "针对抖音实况照片作品，逐图片索引识别静态原图（WebP/JPG）与动态视频片段（MP4），动态视频经 FFprobe 视频流检查通过后，与同编号静态图配对落盘。",
        "trigger": "首页输入框粘贴抖音实况图文链接",
        "code": "app/engine/douyin_probe.py: _download_douyin_live_photo(); app/file_publish.py: publish_pair_image()",
        "status": "生产级就绪",
        "rules": "静态图与动态视频严格同序号同名（如 001.webp 与 001.mp4）；缺少动态流或静态图时提示并做安全降级。"
    },
    {
        "id": "F-PL-004",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "移动端分享长链与短链接自动归一化",
        "desc": "智能解析并兼容 v.douyin.com 短链、iesdouyin.com/share/video/ID、/share/note/ID、/share/slides/ID 及 m.douyin.com 等各种官方移动端分享格式，自动提取目标 awemeId 并归一化为标准作品链接。",
        "trigger": "粘贴移动端 App 复制的任意抖音分享链接或短链",
        "code": "app/platforms.py: identify_url()",
        "status": "生产级就绪",
        "rules": "自动过滤 tracking 参数；拒绝非法或非纯数字作品 ID；重定向逐跳校验。"
    },
    {
        "id": "F-PL-005",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "主页弹窗作品与上下文参数去歧义",
        "desc": "当用户输入包含 modal_id 与 vid 的复合链接（如 user?...modal_id=A&vid=B）时，严格绑定 modal_id 为当前目标作品，消除上下文 vid 引起的歧义与串号风险。",
        "trigger": "在抖音网页端作者主页打开弹窗并复制地址栏链接提交",
        "code": "app/platforms.py: identify_url(); app/engine/discovery.py",
        "status": "生产级就绪",
        "rules": "modal_id 与 vid 同时存在且不合法或冲突时直接报错拒绝，防止误下上下文列表视频。"
    },
    {
        "id": "F-PL-006",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "作者主页批量作品发现与 SecSDK 签名",
        "desc": "优先通过网页端注入的官方 SecSDK 发起前端合法签名请求，若签名超时、接口失效或被拒，平滑安全回退至 Playwright 自动化滚动发现，按页捕获作者全部公开作品。",
        "trigger": "输入抖音作者主页链接（如 douyin.com/user/...）",
        "code": "app/douyin_signing.py: fetch_signed_aweme_detail(); app/douyin.py: discover_profile()",
        "status": "实验性支持",
        "rules": "主页发现包含重试退避机制；对发现的每个作品均核对作者 sec_uid，防止混入推荐流视频。"
    },
    {
        "id": "F-PL-007",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "官方播放路由交错探测与故障转移",
        "desc": "对 default 原始档交错尝试 line=0 与 line=1 官方入口共 4 条 CDN 路由。若首个地址返回低清、超时或跳往非受信域，自动平滑切换至备用候选，不放弃最高画质证据。",
        "trigger": "下载抖音高清视频或实况相片动态流时底层自动执行",
        "code": "app/engine/douyin_probe.py: _probe_candidate_media()",
        "status": "生产级就绪",
        "rules": "已测为低清的地址绝不作为备用源；声明 2K/4K 档位不能被较低档位覆盖。"
    },
    {
        "id": "F-PL-008",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "分片 MP4 时长复核防误判机制",
        "desc": "当媒体文件前 256KB 无法判定整片时长时，下载完整临时文件并使用本地 FFprobe 读取实际时长，与作品声明时长比对（严格容差），杜绝把分片时长误判为全片并误报截断。",
        "trigger": "遇到分片编码或元数据位于文件末尾的抖音视频时触发",
        "code": "app/engine/douyin_probe.py: _verify_full_media_duration()",
        "status": "生产级就绪",
        "rules": "完整文件复核通过后直接原子落盘，绝不重新向 CDN 完整下载第二遍以节约流量。"
    },
    {
        "id": "F-PL-009",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音 (Douyin)",
        "name": "纯匿名会话隔离下载机制",
        "desc": "单个公开作品默认开启 anonymous_douyin=True，采用不携带个人 Chrome Cookie 的隔离网络会话，保护用户个人账号免受平台风控、封控或设备标记。",
        "trigger": "默认配置下下载任意抖音公开视频/图文",
        "code": "app/engine/core.py: DownloaderConfig(anonymous_douyin=True)",
        "status": "生产级就绪",
        "rules": "仅在用户明确在设置中勾选「使用 Chrome 登录态」且退出 Chrome 时才加载对应凭据。"
    },
    {
        "id": "F-PL-010",
        "category": "平台接入与媒体解析",
        "subsystem": "抖音精选 (Selected)",
        "name": "抖音精选网页与移动端作品完整解析",
        "desc": "支持 jingxuan.douyin.com 桌面网页端及 /m/video/ID 移动端作品解析，底层复用成熟的抖音高清视频探测引擎与流校验机制，提取最高可用画质媒体流。",
        "trigger": "粘贴包含 jingxuan.douyin.com 的视频或图文链接",
        "code": "app/platforms.py: identify_url(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "归一化为规范作品 ID；按「抖音」统一目录收纳；同享重名保护与流校验。"
    },
    {
        "id": "F-PL-011",
        "category": "平台接入与媒体解析",
        "subsystem": "小红书 (Xiaohongshu)",
        "name": "官方分享短链接受限解析与参数保留",
        "desc": "对 xhslink.com 和 xhslink.cn 官方分享短链接执行受限单跳重定向解析，强制升级 HTTP 为 HTTPS，完整保留作品分享参数（如 xsec_token），防止风控拦截。",
        "trigger": "输入框粘贴小红书 App 复制的分享短链接",
        "code": "app/xiaohongshu.py: parse_note(); app/platforms.py: extract_url()",
        "status": "生产级就绪",
        "rules": "拦截非法重定向与跨站伪造跳转；仅放行小红书可信域名；对 404/sec 风控精准识别。"
    },
    {
        "id": "F-PL-012",
        "category": "平台接入与媒体解析",
        "subsystem": "小红书 (Xiaohongshu)",
        "name": "普通图文笔记 direct CDN 原图提取与多级回退",
        "desc": "优先根据图片 token 重建 direct CDN 地址获取最高清无压缩大图；读取文件实际像素尺寸，低于作品声明尺寸的占位图自动拒绝；具备 DFT/PRV 多级优雅回退机制。",
        "trigger": "粘贴小红书图文笔记链接进行解析下载",
        "code": "app/xiaohongshu.py: _rebuild_direct_cdn_url(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "本地不二次重压缩；按 001.jpg/webp 短序号收纳至独立作品子文件夹。"
    },
    {
        "id": "F-PL-013",
        "category": "平台接入与媒体解析",
        "subsystem": "小红书 (Xiaohongshu)",
        "name": "视频笔记 originVideoKey 与原视频流选择",
        "desc": "视频作品优先检测并提取 originVideoKey 原始母版流；同时从 media.stream 与 mediaV2 多码流数据中比对实际分辨率、码率和文件大小，选择最高可用画质，严禁封面图冒充视频。",
        "trigger": "粘贴小红书视频笔记链接进行解析下载",
        "code": "app/xiaohongshu.py: parse_note(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "下载后使用 FFprobe 严格校验分辨率与时长，若原视频地址返回低清继续尝试下一候选。"
    },
    {
        "id": "F-PL-014",
        "category": "平台接入与媒体解析",
        "subsystem": "小红书 (Xiaohongshu)",
        "name": "Live Photo 实况照片原图与动态视频成对提取",
        "desc": "对小红书实况照片（Live Photo），静态照片与动态视频作为独立媒体分别提取，动态图自身宽高参与比较，下载后通过 FFmpeg 完整解码校验，并成对同步命名保存（如 001.heic 与 001.mp4）。",
        "trigger": "粘贴小红书实况照片笔记链接进行解析下载",
        "code": "app/xiaohongshu.py: parse_note(); app/file_publish.py: publish_pair_image()",
        "status": "生产级就绪",
        "rules": "静态图与动态视频必须成对验证成功；任意一部分缺失均不判定为完成；成对重名加 (2) 保护。"
    },
    {
        "id": "F-PL-015",
        "category": "平台接入与媒体解析",
        "subsystem": "小红书 (Xiaohongshu)",
        "name": "作者主页滚动发现与增量捕获",
        "desc": "基于 Playwright 自动化驱动无头浏览器访问小红书作者主页，执行平滑滚动与动态内容监听，增量提取主页下的全部笔记条目，逐条去重与过滤。",
        "trigger": "粘贴小红书作者主页链接（xiaohongshu.com/user/profile/...）",
        "code": "app/xiaohongshu.py: discover_profile()",
        "status": "生产级就绪",
        "rules": "支持取消操作；记录作者 profile_id；主页作品去重与断点恢复。"
    },
    {
        "id": "F-PL-016",
        "category": "平台接入与媒体解析",
        "subsystem": "小红书 (Xiaohongshu)",
        "name": "官方风控与 404 状态精准识别与引导",
        "desc": "精准捕获小红书官方返回的 /404/ 页面、sec 验证或“内容暂不可浏览”状态，准确上报为内容失效或需要验证，不误报为通用跨站重定向错误或底层网络断连。",
        "trigger": "遇到已被作者删除、私密或触发小红书平台风控的链接时",
        "code": "app/xiaohongshu.py: _open_xiaohongshu_response()",
        "status": "生产级就绪",
        "rules": "向前端提供明确的 SiteIssueCode 分类与针对性解决指引。"
    },
    {
        "id": "F-PL-017",
        "category": "平台接入与媒体解析",
        "subsystem": "小红书 (Xiaohongshu)",
        "name": "一键唤起 Chrome 解决人机滑块验证",
        "desc": "当小红书作品请求触发安全滑块或需登录时，任务状态置为 NEEDS_AUTH，用户在界面点击「打开验证」，系统自动使用目标 Note 绑定的精确 URL 打开系统 Chrome，用户滑块通过后即可一键重试。",
        "trigger": "任务卡片显示需处理且提示验证时，点击「打开验证」按钮",
        "code": "app/main.py: open_verification(); app/browser.py: open_chrome()",
        "status": "生产级就绪",
        "rules": "严格校验重定向 URL 归属性与 note_id 绑定，绝不跳转不可信第三方网址。"
    },
    {
        "id": "F-PL-018",
        "category": "平台接入与媒体解析",
        "subsystem": "快手 (Kuaishou)",
        "name": "全网关与短链接智能识别",
        "desc": "全面支持 kuaishou.com、v.kuaishou.com、m.kuaishou.com、c.kuaishou.com、m.gifshow.com 以及动态移动网关 v.m.chenzhongtech.com 等各类型快手分享链接识别与规范化处理。",
        "trigger": "输入框粘贴快手 App 复制的任意分享链接或短链",
        "code": "app/platforms.py: identify_url(); app/kuaishou.py: source_identity()",
        "status": "生产级就绪",
        "rules": "自动过滤移动网关冗余参数；严格校验合法域名族与标准协议。"
    },
    {
        "id": "F-PL-019",
        "category": "平台接入与媒体解析",
        "subsystem": "快手 (Kuaishou)",
        "name": "浏览器正常响应被动观察机制",
        "desc": "基于 Playwright 启动真实浏览器环境观察快手正常页面请求与响应数据，避免盲目重放私有高风险加密签名 API，极大降低触发快手反爬风控的概率。",
        "trigger": "解析快手单视频、图集或作者主页时底层自动执行",
        "code": "app/kuaishou.py: discover()",
        "status": "生产级就绪",
        "rules": "仅读取已授权白名单域名数据；严格限制单次响应大小不超过 16MB。"
    },
    {
        "id": "F-PL-020",
        "category": "平台接入与媒体解析",
        "subsystem": "快手 (Kuaishou)",
        "name": "普通短视频最高画质解析与落盘",
        "desc": "从观察到的正常会话响应中提取可信 CDN（kwaicdn.com, yximgs.com 等）的高清 MP4 直链，经音视频流完整性校验后保存，保留发布日期与作者信息。",
        "trigger": "粘贴快手单视频链接发起下载",
        "code": "app/kuaishou.py: _extract_video_item(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "仅限受信快手媒体 CDN；FFprobe 校验流正常；不二次压缩。"
    },
    {
        "id": "F-PL-021",
        "category": "平台接入与媒体解析",
        "subsystem": "快手 (Kuaishou)",
        "name": "横版图集 (HORIZONTAL_ATLAS) 完整批量提取",
        "desc": "针对快手横版图集作品，严格核对页面声明的原始图片总数、索引与作者身份，批量下载全部原始高分辨率 WebP 大图，数量不完整时明确拦截报错。",
        "trigger": "粘贴快手多图横版图集作品链接发起下载",
        "code": "app/kuaishou.py: _extract_atlas_item(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "短序号规范命名（如 001.webp）；经 FFmpeg 全量解码验证；原图不压缩。"
    },
    {
        "id": "F-PL-022",
        "category": "平台接入与媒体解析",
        "subsystem": "快手 (Kuaishou)",
        "name": "竖版超长图 (VERTICAL_ATLAS) 瓦片切片解析",
        "desc": "针对快手服务端以瓦片（Tile Slices）形式下发的竖版长图，按顺序下载全部 16 个平台原始切片并完整落盘，在界面清晰标注切片特性，杜绝强行失真拼接。",
        "trigger": "遇到快手长图/漫画作品时触发",
        "code": "app/kuaishou.py: _extract_vertical_atlas_item(); app/engine/platform_items.py",
        "status": "部分支持 / 瓦片切片",
        "rules": "每个切片独立解码校验；明确向用户提示切片边界；保留原始文件。"
    },
    {
        "id": "F-PL-023",
        "category": "平台接入与媒体解析",
        "subsystem": "快手 (Kuaishou)",
        "name": "作者主页有界分页遍历与安全退避",
        "desc": "支持快手作者主页滚动遍历（最大限制 500 页 / 10,000 项），具备指数退避重试机制；若未获得服务端明确的末页结束证据，自动标记为不完整并安全保留已保存文件。",
        "trigger": "输入快手作者主页链接进行批量作品发现",
        "code": "app/kuaishou.py: _paginate_profile()",
        "status": "待真实站点验收",
        "rules": "遇到频率限制自动退避重试（最多3次）；可恢复中断保留已完成任务进度。"
    },
    {
        "id": "F-PL-024",
        "category": "平台接入与媒体解析",
        "subsystem": "哔哩哔哩 (Bilibili)",
        "name": "单视频全清晰度无损合并下载",
        "desc": "基于成熟强大的 yt-dlp 核心引擎，自动探测并抓取 B站可用的最高画质视频流与音频流（bestvideo*+bestaudio/best，含 1080p, 4K, 8K），调用本地 FFmpeg 执行无损 Remux 封装，绝不进行画质劣化的二次重编码。",
        "trigger": "输入框粘贴 B站视频链接（如 bilibili.com/video/BV...）",
        "code": "app/engine/core.py: MediaDownloader._download_with_ytdlp()",
        "status": "生产级就绪",
        "rules": "公开最高画质免登录；大会员/高码率可通过配置 Chrome Cookie 获取；自动按「B站/作者」归档。"
    },
    {
        "id": "F-PL-025",
        "category": "平台接入与媒体解析",
        "subsystem": "哔哩哔哩 (Bilibili)",
        "name": "分P视频指定或全选集下载",
        "desc": "智能解析 B站视频 URL 中的分P参数（如 ?p=2），支持仅下载指定单分P，也可通过选集弹窗多选指定分P，或批量下载全部多P剧集，自动以分P标题命名。",
        "trigger": "粘贴带分P参数的链接，或点击「预览选集」勾选下载",
        "code": "app/platforms.py: identify_url(); app/engine/core.py",
        "status": "生产级就绪",
        "rules": "多P支持独立进度监控与断点恢复；每个分P核验音视频完整性。"
    },
    {
        "id": "F-PL-026",
        "category": "平台接入与媒体解析",
        "subsystem": "哔哩哔哩 (Bilibili)",
        "name": "合集、系列与播单批量展开与排队",
        "desc": "支持 space.bilibili.com/.../lists/9508534 等合集、系列（Season）、收藏夹及播放列表链接，自动识别为 PLAYLIST 模式，展开所有单视频并批量生成下载任务。",
        "trigger": "粘贴 B站合集、列表或系列链接",
        "code": "app/platforms.py: identify_url(); app/engine/discovery.py",
        "status": "生产级就绪",
        "rules": "自动去重；支持暂停与批量管理；每个作品独立核验流状态。"
    },
    {
        "id": "F-PL-027",
        "category": "平台接入与媒体解析",
        "subsystem": "哔哩哔哩 (Bilibili)",
        "name": "图文动态与专栏解析 (Opus & CV)",
        "desc": "专门实现 bilibili_opus.py 模块，全面解除对 /opus/ 和 /read/cv 链接的拦截，支持专栏短链跳转识别，自动拉取文章正文文本及最高分辨率原始配图并落盘。",
        "trigger": "粘贴 B站专栏链接（/read/cv...）或动态图文链接（/opus/...）",
        "code": "app/bilibili_opus.py: parse_bilibili_opus(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "仅限 hdslb.com, biliimg.com 等官方媒体域名；配图逐跳安全校验；支持超长条图解码校验。"
    },
    {
        "id": "F-PL-028",
        "category": "平台接入与媒体解析",
        "subsystem": "YouTube",
        "name": "长视频全规格最高画质 Remux 下载",
        "desc": "基于 yt-dlp 提取 YouTube 最高可用音视频流（支持 1080p, 2K, 4K, 8K 等高码率源），内置 Deno 运行时自动应对 YouTube 最新 JS 挑战算法，通过 FFmpeg 无损合并封包。",
        "trigger": "输入框粘贴 YouTube 视频链接（youtube.com/watch?v=... 或 youtu.be/...）",
        "code": "app/engine/core.py: MediaDownloader._download_with_ytdlp()",
        "status": "生产级就绪",
        "rules": "公开内容免登录；需本机安装有 FFmpeg；无损 Remux 不损失画面质量。"
    },
    {
        "id": "F-PL-029",
        "category": "平台接入与媒体解析",
        "subsystem": "YouTube",
        "name": "Shorts 短视频极速识别与抓取",
        "desc": "智能识别 youtube.com/shorts/ID 路径结构，将其归一化为标准视频实体，提取最高清晰度的竖版无水印视频流并进行文件归档。",
        "trigger": "粘贴 YouTube Shorts 链接提交",
        "code": "app/platforms.py: identify_url(); app/engine/core.py",
        "status": "生产级就绪",
        "rules": "归入「YouTube」平台目录；自动重名保护；支持查重检测。"
    },
    {
        "id": "F-PL-030",
        "category": "平台接入与媒体解析",
        "subsystem": "YouTube",
        "name": "频道全量视频与播放列表批量解析",
        "desc": "支持 YouTube 频道首页（@handle、/channel/、/c/ 等）以及播放列表（playlist?list=...）链接识别，自动展开全部视频条目并批量加入下载队列。",
        "trigger": "粘贴 YouTube 频道主页或播放列表链接",
        "code": "app/platforms.py: identify_url(); app/engine/discovery.py",
        "status": "生产级就绪",
        "rules": "支持大规模列表排队与逐项重试；遵循平台访问限制与重试策略。"
    },
    {
        "id": "F-PL-031",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 (Channels)",
        "name": "视频号分享短链与落地页智能提取",
        "desc": "识别并提取 weixin.qq.com/sph/ 短链接及 channels.weixin.qq.com/finder-preview/pages/ 落地页，严格校验协议合法性与分享码格式，拒绝带鉴权串或非标准端口的链接。",
        "trigger": "粘贴微信内复制的视频号分享链接或整段分享文案",
        "code": "app/platforms.py: identify_url(); app/wechat.py: _share_code()",
        "status": "生产级就绪",
        "rules": "仅限 HTTPS 标准 443 端口；必须是微信官方合规分享落地地址。"
    },
    {
        "id": "F-PL-032",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 (Channels)",
        "name": "腾讯元宝官方 Session 会话桥接解析",
        "desc": "利用用户本人的腾讯元宝登录态，调用元宝官方接口换取带鉴权的合法播放直链 playable_url（包含 token 与 exportId），进一步调用微信底层接口获取高清视频流与动态解密秘钥。",
        "trigger": "提交视频号链接后后台调度执行",
        "code": "app/wechat.py: profile_yuanbao(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "不使用公共账号池或第三方中转；用户凭据完全隔离在本地；支持嵌套视频信息读取。"
    },
    {
        "id": "F-PL-033",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 (Channels)",
        "name": "内置独立浏览器自动化扫码登录元宝",
        "desc": "在设置面板提供「扫码登录元宝」按钮，基于 Playwright 弹出独立的无干扰浏览器窗口，引导用户扫码或登录元宝，程序自动监听并截获有效 Cookie 并加密保存，彻底免除手动抓包。",
        "trigger": "打开设置 -> 视频号设置 -> 点击「扫码登录元宝」",
        "code": "app/wechat.py: login_with_browser(); app/main.py: /api/wechat/login",
        "status": "生产级就绪",
        "rules": "配备全局并发锁防止重复弹窗；超时时间长达 5-6 分钟；内存与日志全程脱敏。"
    },
    {
        "id": "F-PL-034",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 (Channels)",
        "name": "专属 Chrome 扩展免抓包复制登录态",
        "desc": "配套提供 extension/yuanbao-login-copy 扩展程序。用户在 Chrome 加载扩展后，在元宝页面点击插件图标即可一键把未过期的元宝 Cookie 复制到剪贴板，随后直接粘贴保存。",
        "trigger": "在 Chrome 中加载扩展并点击扩展弹窗按钮",
        "code": "extension/yuanbao-login-copy/popup.js",
        "status": "生产级就绪",
        "rules": "扩展仅在本地读取指定域名 Cookie，不联网、不上传、无第三方依赖。"
    },
    {
        "id": "F-PL-035",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 (Channels)",
        "name": "Windows DPAPI 本机数据安全加密存储",
        "desc": "视频号元宝 Cookie 采用 Windows 系统当前用户级别的 DPAPI（CryptProtectData）进行密文加密，持久化保存在 data/yuanbao-cookie.txt，前端接口绝不反显明文 Cookie，防止凭据泄露。",
        "trigger": "保存视频号 Cookie 时系统底层自动执行",
        "code": "app/wechat_secret.py: save_secret(), load_secret()",
        "status": "生产级就绪",
        "rules": "仅限当前 Windows 登录用户解密；读取明文时自动完成透明加密升级；支持一键清除。"
    },
    {
        "id": "F-PL-036",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 (Channels)",
        "name": "ISAAC64 流密码二进制解密引擎",
        "desc": "针对微信视频号下发的经过 ISAAC64 算法加密的视频二进制流，程序动态提取返回的 64 位 decode_key，对视频前 128KB（131072 字节）执行实时对称异或解密，还原为标准合规 MP4 容器。",
        "trigger": "分块下载视频号媒体流时在内存流式执行",
        "code": "app/wechat_isaac.py: decrypt_channels_data(); app/wechat.py: _download_stream_and_decrypt()",
        "status": "生产级就绪",
        "rules": "流式原地解密并直接写入暂存文件；解密后使用 FFprobe 严格验证音视频流时长与有效性。"
    },
    {
        "id": "F-PL-037",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 (Channels)",
        "name": "严格作品身份绑定与防串号拦截",
        "desc": "核对元宝返回的 feedInfo 中的 shortUri 与用户提交的原始分享码，严格核对 objectId，确保获取的播放地址与用户请求的作品 100% 一致，若不匹配立即停止下载，杜绝推荐串号。",
        "trigger": "元宝解析接口返回数据后自动校验",
        "code": "app/wechat.py: profile_yuanbao(), work_identity()",
        "status": "生产级就绪",
        "rules": "仅限 finder.video.qq.com 官方直链；重定向超 6 次或非标准端口一律拒绝。"
    },
    {
        "id": "F-PL-038",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 (Channels)",
        "name": "无 Content-Length 流式传输与取消响应",
        "desc": "针对部分 CDN 不返回 Content-Length 头部的情况，采用块分片迭代读取，实时计算已读字节数与下载速率，在缺少总大小时也能正常展示进度条并在遇到取消时毫秒级停止响应。",
        "trigger": "下载不带长度头的流媒体时自动触发",
        "code": "app/wechat.py: _download_stream_and_decrypt(); app/engine/platform_items.py",
        "status": "生产级就绪",
        "rules": "取消时立即删除未完成的 .part 文件；不产生磁盘碎片垃圾。"
    },

    # -------------------------------------------------------------
    # 二、下载核心引擎与媒体管线 (14项)
    # -------------------------------------------------------------
    {
        "id": "F-ENG-001",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "分段传输与断点续传",
        "name": "分段临时缓存隔离机制",
        "desc": "每次传输统一在 cache/缓存编号/ 建立单层隔离目录，临时文件以 .part 后缀命名，确保并发下载或不同任务间互不干扰、互不覆盖。",
        "trigger": "发起任何视频或图文文件下载时自动创建",
        "code": "app/engine/transfer.py; app/downloader.py",
        "status": "生产级就绪",
        "rules": "传输完成自动清理当前临时目录；异常中断时安全保留以便断点续传。"
    },
    {
        "id": "F-ENG-002",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "分段传输与断点续传",
        "name": "HTTP Range 智能断点续传支持",
        "desc": "下载因网络断开、系统崩溃或用户手动暂停中断后，重新启动时自动读取已有 .part 文件大小，发起带有 Range 请求头的 HTTP 请求，实现无损断点续传。",
        "trigger": "任务从 PAUSED 或 INTERRUPTED 状态继续恢复下载时",
        "code": "app/engine/transfer.py: _download_stream()",
        "status": "生产级就绪",
        "rules": "比对 ETag 或内容指纹，若源端已发生变动则自动重置并重新拉取。"
    },
    {
        "id": "F-ENG-003",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "分段传输与断点续传",
        "name": "失败临时缓存安全清理与保留策略",
        "desc": "当下载发生不可逆错误或用户显式取消时，自动清理当前作品生成的损坏 .part 暂存文件；对于已证实完好的分片安全保留，防止磁盘空间无故膨胀。",
        "trigger": "下载出错或用户点击取消时触发",
        "code": "app/engine/transfer.py; app/file_publish.py",
        "status": "生产级就绪",
        "rules": "防止残留半截损坏文件被误作为完整文件引用。"
    },
    {
        "id": "F-ENG-004",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "原子落盘与去重保护",
        "name": "原子无覆盖文件落盘机制 (publish_no_replace)",
        "desc": "文件校验合格后，优先基于操作系统的硬链接 (os.link) 或同卷原子重命名 (os.rename) 移入正式目录，杜绝多线程写入冲突与对已有同名文件的静默覆盖。",
        "trigger": "下载完成且音视频/图片校验通过落盘时",
        "code": "app/file_publish.py: publish_no_replace()",
        "status": "生产级就绪",
        "rules": "Windows 同卷 rename 保证原子性；遇到目标已存在引发 FileExistsError 并交由避让逻辑处理。"
    },
    {
        "id": "F-ENG-005",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "原子落盘与去重保护",
        "name": "自动重名序号避让命名 (publish_unique)",
        "desc": "若下载目录下已存在同名旧文件，系统绝不覆盖原文件，而是自动以递增编号重命名为 文件名 (2).扩展名、文件名 (3).扩展名，支持多达 10,000 次避让探测。",
        "trigger": "重复下载相同标题作品或同一作者发布同名作品时",
        "code": "app/file_publish.py: publish_unique()",
        "status": "生产级就绪",
        "rules": "保护已有历史文件；原子测试每个 candidate 文件名直至成功。"
    },
    {
        "id": "F-ENG-006",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "原子落盘与去重保护",
        "name": "相同内容智能哈希复用 (publish_or_reuse_identical)",
        "desc": "在重名检查时，若发现本地已有文件与当前下载的临时文件在文件大小及首尾/全量哈希上 100% 相同，自动直接复用该文件并删除临时缓存，避免重复占用磁盘与浪费带宽。",
        "trigger": "用户选择「仍然下载」重复链接时",
        "code": "app/file_publish.py: are_files_identical(), publish_or_reuse_identical()",
        "status": "生产级就绪",
        "rules": "先检查尺寸，小于 1MB 读全量，大于 1MB 检验首尾 64KB 块，快速精准。"
    },
    {
        "id": "F-ENG-007",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "原子落盘与去重保护",
        "name": "Live Photo 实况成对素材同步避让 (publish_pair_image)",
        "desc": "针对实况照片的静态图与动态视频，采用专用进程级锁同步命名决策，确保当发生重名时，静态图与动态视频严格同步分配相同的序号（如 001 (2).heic 与 001 (2).mp4）。",
        "trigger": "落盘 Live Photo 实况照片的静态和动态组件时",
        "code": "app/file_publish.py: publish_pair_image()",
        "status": "生产级就绪",
        "rules": "杜绝静态图加了 (2) 而动态视频加了 (3) 的错位现象，保证素材严格配对。"
    },
    {
        "id": "F-ENG-008",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "目录规范与文件命名",
        "name": "平台与作者二级结构化归档",
        "desc": "下载内容按「平台名称 -> 作者昵称」二级目录自动分类归档，如 downloads/抖音/新华社/、downloads/B站/央视新闻/，不同平台与作者条理分明。",
        "trigger": "创建任务并生成输出路径时自动规划",
        "code": "app/engine/naming.py: _platform_output_directory(); 命名与目录规范.md",
        "status": "生产级就绪",
        "rules": "平台名规范统一（抖音、快手、小红书、B站、YouTube、视频号）；未知作者保底为「未知作者」。"
    },
    {
        "id": "F-ENG-009",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "目录规范与文件命名",
        "name": "规范化视频文件全息命名",
        "desc": "视频文件采用 YYYY-MM-DD-作品标题 [作品ID].扩展名 规范命名。若无法取得日期则为 Unknown-Date，非法文件名字符自动替换，超长标题安全截短但保留作品 ID。",
        "trigger": "视频文件最终写入磁盘前格式化文件名",
        "code": "app/engine/naming.py; app/downloader.py: safe_component()",
        "status": "生产级就绪",
        "rules": "防系统路径超长越界；保留真实作品 ID 以便历史溯源与对账。"
    },
    {
        "id": "F-ENG-010",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "目录规范与文件命名",
        "name": "图文作品独立子目录与短序号收纳",
        "desc": "图文与多图笔记在作者目录下单独建立作品文件夹（如 小红书/作者/日期-标题 [ID]/），内含素材使用短序号规范命名（如 001.webp, 002.jpg），干净整洁。",
        "trigger": "下载小红书图文、抖音图文、快手图集时自动创建",
        "code": "app/engine/naming.py; 命名与目录规范.md",
        "status": "生产级就绪",
        "rules": "批量下载图文作品时每个作品独立建文件夹，互不混杂。"
    },
    {
        "id": "F-ENG-011",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "媒体校验与画质复核",
        "name": "FFprobe 格式流与时长音轨多维复核",
        "desc": "所有视频文件在标记成功前，强制调用本地 FFprobe 读取 stream 元数据，校验编码格式、实际分辨率、有效时长及是否存在有效音轨，流损坏文件拒绝标记完成。",
        "trigger": "视频文件传输完毕落盘前自动执行",
        "code": "app/engine/douyin_probe.py; app/wechat.py: verify_video()",
        "status": "生产级就绪",
        "rules": "杜绝把下载失败的 404 HTML 或 0 字节损坏文件当成视频发布。"
    },
    {
        "id": "F-ENG-012",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "媒体校验与画质复核",
        "name": "FFmpeg 图片全量解码完整性校验",
        "desc": "实现 _decode_local_image 函数，对已下载的图片调用 FFmpeg 进行完整位图解码测试，防止因网络中断仅下载了文件头部但图像数据截断的文件被误认为成功。",
        "trigger": "图片素材落盘前或复用已有图片时自动触发",
        "code": "app/engine/transfer.py: _decode_local_image()",
        "status": "生产级就绪",
        "rules": "截断或解码报错的图片必须重新拉取，确保图集完整无坏图。"
    },
    {
        "id": "F-ENG-013",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "媒体校验与画质复核",
        "name": "媒体多格式 Magic Bytes 识别与扩展名校正",
        "desc": "根据文件二进制前缀特征魔数（Magic Bytes）准确识别 JPEG, PNG, WebP, AVIF, GIF, HEIC 等真实图片格式，保留 .heic 等真实扩展名，不强行转换或篡改格式。",
        "trigger": "从 CDN 下载原始媒体流识别文件类型时",
        "code": "app/engine/transfer.py: _sniff_image_format()",
        "status": "生产级就绪",
        "rules": "遵循平台原始编码格式，保持原汁原味。"
    },
    {
        "id": "F-ENG-014",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "媒体校验与画质复核",
        "name": "音视频流无损 Remux 封包合并",
        "desc": "对于 B站、YouTube 等平台分离下发的独立视频流与音频流，仅调用 FFmpeg 进行流合并（Remuxing，-c copy），绝不对音视频画面执行任何重编码有损压缩。",
        "trigger": "下载完分离音视频流后自动执行封包",
        "code": "app/engine/core.py: MediaDownloader",
        "status": "生产级就绪",
        "rules": "不消耗过多 CPU 算力；画面保真度与音质达到 100% 原始位级别。"
    },
]

print(f"Loaded {len(ALL_FEATURES)} features so far.")

print(f'Total features now: {len(ALL_FEATURES)}')


ALL_FEATURES.extend(MGR_AND_UI_FEATURES)
print(f'Total features now: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(API_DSK_SEC_FEATURES)
print(f'Total features in full matrix: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(MGR_AND_UI_FEATURES)
print(f'Total features now: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(API_DSK_SEC_FEATURES)
print(f'Total features in full matrix: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(MGR_AND_UI_FEATURES)
print(f'Total features now: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(API_DSK_SEC_FEATURES)
print(f'Total features in full matrix: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(MGR_AND_UI_FEATURES)
print(f'Total features now: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(API_DSK_SEC_FEATURES)
print(f'Total features in full matrix: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(MGR_AND_UI_FEATURES)
print(f'Total features now: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(API_DSK_SEC_FEATURES)
print(f'Total features in full matrix: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(MGR_AND_UI_FEATURES)
print(f'Total features now: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(API_DSK_SEC_FEATURES)
print(f'Total features in full matrix: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(MGR_AND_UI_FEATURES)
print(f'Total features now: {len(ALL_FEATURES)}')

ALL_FEATURES.extend(API_DSK_SEC_FEATURES)
print(f'Total features in full matrix: {len(ALL_FEATURES)}')
