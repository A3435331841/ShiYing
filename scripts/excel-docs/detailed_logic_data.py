# -*- coding: utf-8 -*-
"""Ultra-detailed logic and boundary conditions dataset for Shiying project."""

DETAILED_LOGIC_BRANCHES = [
    # =========================================================================
    # 1. 平台接入与媒体逆向 (Platform Ingestion & Reverse Engineering)
    # =========================================================================
    {
        "id": "DL-PL-001",
        "category": "平台接入与媒体逆向",
        "subsystem": "URL输入提取与清洗",
        "name": "中文分享文案与混杂文本智能提取",
        "condition": "用户输入包含汉字、表情、多余介绍字符及一个标准 HTTP/HTTPS 链接",
        "logic": "通过 _URL_RE 正则表达式匹配首个完整 http:// 或 https:// 协议头链接，自动剥离前后多余文字与空白字符",
        "code": "app/platforms.py:extract_url",
        "result": "精准返回纯净 URL 字符串；未提取到有效 URL 则抛出 UnsupportedUrlError('Please enter a complete http:// or https:// URL')",
        "safety": "拒绝非 http/https 协议；拒绝无 hostname 的非法串"
    },
    {
        "id": "DL-PL-002",
        "category": "平台接入与媒体逆向",
        "subsystem": "URL输入提取与清洗",
        "name": "Markdown 格式超链接优先提取",
        "condition": "输入文本中同时包含 Markdown 格式链接 [标题](https://...) 与裸链接",
        "logic": "正则 _MARKDOWN_URL_RE 与 _URL_RE 并行探测，若 markdown 匹配位置早于或等于裸链接位置，优先提取括号内的目标 URL",
        "code": "app/platforms.py:extract_url",
        "result": "提取 Markdown 语法目标 URL，忽略展示文本部分",
        "safety": "防止剪贴板复制富文本时将方括号标题混入 URL"
    },
    {
        "id": "DL-PL-003",
        "category": "平台接入与媒体逆向",
        "subsystem": "URL输入提取与清洗",
        "name": "分享文本末尾中英文标点自动剥离",
        "condition": "复制的分享链接末端带有句号、逗号、感叹号、问号、方括号或中文标点（如 。，！？】）",
        "logic": "调用 rstrip(_TRAILING_SHARE_PUNCTUATION)，自动剥除包含 \").,;!?]}。，；！？】）\" 在内的所有尾随标点",
        "code": "app/platforms.py:extract_url:32",
        "result": "返回不带末尾标点的合法 URL，避免 HTTP 404",
        "safety": "防止第三方 App 分享文案末尾标点污染 URL path"
    },
    {
        "id": "DL-PL-004",
        "category": "平台接入与媒体逆向",
        "subsystem": "URL输入提取与清洗",
        "name": "含账号密码凭证 URL 严格拦截",
        "condition": "用户输入的 URL 中包含用户名或密码部分（例如 http://user:pass@example.com）",
        "logic": "解析 parsed.username 与 parsed.password，只要任一不为 None 即刻抛出异常",
        "code": "app/platforms.py:extract_url:38",
        "result": "抛出 UnsupportedUrlError('URLs containing credentials are not accepted')",
        "safety": "防止凭证泄露或恶意 URL 混淆攻击"
    },
    {
        "id": "DL-PL-005",
        "category": "平台接入与媒体逆向",
        "subsystem": "URL输入提取与清洗",
        "name": "非法端口号拦截与 URL 归一化",
        "condition": "输入带有端口号的 URL（如 :8080 或非法端口串）",
        "logic": "尝试提取 parsed.port，捕获 ValueError；归一化 scheme 为小写、netloc 小写并去除末尾点，剥离 fragment 锚点",
        "code": "app/platforms.py:extract_url:40",
        "result": "非法端口报 'Invalid URL port'；合法则返回无 #fragment 的标准 URL",
        "safety": "防范端口溢出与异常端口扫描"
    },
    {
        "id": "DL-PL-006",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "抖音 v.douyin.com 短链识别",
        "condition": "host 严格等于 v.douyin.com",
        "logic": "识别为 Platform.DOUYIN，SourceKind.SHORT_LINK",
        "code": "app/platforms.py:identify_url:84",
        "result": "标记为短链接形态，后续触发跟随 302 重定向解析",
        "safety": "域名严格匹配，不放行第三方伪造短链"
    },
    {
        "id": "DL-PL-007",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "抖音网页版 share 分享链重写",
        "condition": "host 为 iesdouyin.com/www.iesdouyin.com/m.douyin.com 且 path 匹配 /share/(video|note|slides)/(\\d+)",
        "logic": "正则提取纯数字 ID，重写为标准规范链接 https://www.douyin.com/video/{media_id}，类型置为 ITEM",
        "code": "app/platforms.py:identify_url:87",
        "result": "抹平旧版与移动端分享页差异，统一为标准单作品路由",
        "safety": "防旧接口废弃风险"
    },
    {
        "id": "DL-PL-008",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "抖音 modal_id 与 vid 歧义校验与锚定",
        "condition": "URL 为作者主页 /user/xxx 但 Query 参数中带有 modal_id 或 vid",
        "logic": "若 modal_id 与 vid 列表包含多个值且不一致，或非纯数字，抛异常；若合法，优先取 modal_id 并转化为 /video/{id} 单作品",
        "code": "app/platforms.py:identify_url:109",
        "result": "防止弹窗播放作品与上下文 vid 冲突，严格锚定单作品下载",
        "safety": "抛出 'Invalid or ambiguous Douyin modal/vid video URL'"
    },
    {
        "id": "DL-PL-009",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "抖音精选移动端网页路径重定向",
        "condition": "host 为 jingxuan.douyin.com 且 path 匹配 /m/video/(\\d+)",
        "logic": "提取作品 ID，重组为 https://www.douyin.com/video/{media_id} 单作品",
        "code": "app/platforms.py:identify_url:102",
        "result": "无缝纳入抖音主干高画质探测管线",
        "safety": "复用同一套严格防串号逻辑"
    },
    {
        "id": "DL-PL-010",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "抖音单个作品匿名会话隔离解析",
        "condition": "解析单个普通抖音作品链接（非主页）",
        "logic": "设置 anonymous_douyin=True，强制隔离用户本地 Cookie，不携带任何登录凭据发起无痕请求",
        "code": "app/engine/core.py:discover",
        "result": "成功获取公开作品原始数据，避免污染或暴露本地登录态",
        "safety": "零隐私泄露风险，杜绝风控连带封号"
    },
    {
        "id": "DL-PL-011",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "SecSDK 签名与 a_bogus 参数生成",
        "condition": "请求抖音官方核心 API 接口时需要签名",
        "logic": "调用 fetch_signed_aweme_detail，通过内部算法生成签名并附加 XBogus/a_bogus 到 Query 参数",
        "code": "app/douyin_signing.py:fetch_signed_aweme_detail",
        "result": "接口顺利放行返回数据；返回 status_code=0",
        "safety": "合法拟合客户端签名规范"
    },
    {
        "id": "DL-PL-012",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "Playwright 无头浏览器 JS 签名兜底",
        "condition": "本地或远程签名服务超时、返回空或抛出异常时",
        "logic": "自动唤醒本地 Playwright 无头实例，在真实隔离浏览器上下文中注入并执行签名函数获取加密 Token",
        "code": "app/douyin_signing.py:_eval_in_browser",
        "result": "无缝承接签名任务，确保解析不中断",
        "safety": "环境异常时自动平滑降级"
    },
    {
        "id": "DL-PL-013",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "抖音双源探测：创作者流 vs 默认分发流",
        "condition": "开始获取抖音视频作品的高清媒体源",
        "logic": "并行/交替请求 author-feed（创作者原始档）与 default/amemv（通用分发流），比对两者的分辨率与编码格式",
        "code": "app/engine/douyin_probe.py",
        "result": "若创作者流清晰度高于默认流，优先选择创作者流",
        "safety": "双流均进行时长与 ID 比对校验"
    },
    {
        "id": "DL-PL-014",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "官方 CDN 双路由 Line0/Line1 轮询交错重试",
        "condition": "探测 default 播放流时，单条路由可能受限或超时",
        "logic": "生成4个候选URL：(SNSSDK, line0) -> (RATIO, line1) -> (RATIO, line0) -> (SNSSDK, line1) 依次重试",
        "code": "app/engine/douyin_probe.py:_douyin_default_probe_urls",
        "result": "克服单一 CDN 线路卡死问题，最大化提升连接成功率",
        "safety": "只要任一官方路由返回有效媒体流即刻命中"
    },
    {
        "id": "DL-PL-015",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "非受信域名 302 重定向拦截",
        "condition": "抖音播放链接重定向跳转至未授权的第三方第三方主机",
        "logic": "重定向时校验 Location 域，非 *.snssdk.com, *.douyinvod.com 等官方白名单时抛出 _DouyinRedirectRejected",
        "code": "app/engine/douyin_probe.py",
        "result": "阻止重定向，作为阻塞性安全异常向上抛出，终止下载",
        "safety": "防范 DNS 劫持与恶意流注入"
    },
    {
        "id": "DL-PL-016",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "无进度预算超时控制 (No-Progress Budget)",
        "condition": "网络连接挂起，CDN 无响应但未断开",
        "logic": "初始化 _DouyinNoProgressBudget，超时设为 DOUYIN_QUALITY_PROBE_IDLE_TIMEOUT_SECONDS，超时未读到字节抛 _DouyinNoProgressTimeout",
        "code": "app/engine/douyin_probe.py:105",
        "result": "及时释放挂起线程，避免占用并发槽位",
        "safety": "分类为 SiteIssueCode.SITE_RESPONSE_CHANGED"
    },
    {
        "id": "DL-PL-017",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "FFprobe 实测媒体码率与真实宽高像素",
        "condition": "获取到媒体流前部数据块",
        "logic": "调用本地 FFprobe 进程，探测真实视频宽高（width, height）、视频编码格式（vcodec）与真实码率（bitrate）",
        "code": "app/engine/douyin_probe.py",
        "result": "获取真实物理规格，过滤虚假 1080P/4K 伪高清标记",
        "safety": "若 FFprobe 缺失则提示安装并降级"
    },
    {
        "id": "DL-PL-018",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "视频分片时长与 API 声明时长复核",
        "condition": "探测到的视频实际播放时长与 API 元数据中的 duration_ms 差异显著",
        "logic": "计算实际时长与元数据时长差值，若时长缩水严重（如超过 30% 且缺失分片），判定分片不全抛出异常",
        "code": "app/engine/douyin_probe.py",
        "result": "拒绝不完整截断视频，防止用户下载残次文件",
        "safety": "确保音画完整性"
    },
    {
        "id": "DL-PL-019",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "严格双层 awemeId 核验防串号",
        "condition": "解析返回作品详情字典",
        "logic": "校验 detail.get('aweme_id') 必须等于请求的目标 ID，严禁以所属合集/图集 groupId 替代作品 ID",
        "code": "app/engine/douyin_media.py",
        "result": "若 ID 不符直接抛出异常，拒绝执行后续下载",
        "safety": "杜绝作者视频与他人视频混淆下载"
    },
    {
        "id": "DL-PL-020",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "普通图集作品顺序保全与批量打包",
        "condition": "作品类型为图集 (aweme_type==68 或包含 images 列表)",
        "logic": "提取全部图片 download_url_list 中最高清图源，按作品展示原始顺序分配 asset_index (1, 2, 3...)",
        "code": "app/engine/douyin_media.py",
        "result": "以 {YYYY-MM-DD}-{Title} [{ID}]-001.webp 序号规整落盘",
        "safety": "任意单张失败不阻断其他图片下载"
    },
    {
        "id": "DL-PL-021",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "实况 LivePhoto 静态图与动态视频成对配准",
        "condition": "作品中包含 Live Photo 实况照片（含有静态图像与对应的 live_photo 动态视频流）",
        "logic": "提取两者媒体流，将其配准在同一 DownloadItem 中，分别指定扩展名（通常为 webp/jpg 与 mp4）",
        "code": "app/engine/douyin_media.py",
        "result": "下载引擎成对处理，使用同一基础文件名发布",
        "safety": "确保图与动态视频同名成对，方便在系统相册浏览"
    },
    {
        "id": "DL-PL-022",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "作者主页深度滚动与游标流转",
        "condition": "输入抖音作者主页链接 /user/xxx",
        "logic": "使用 max_cursor 游标依次请求作者作品分页列表，自动合并作品列表，直到 has_more 为 0 或触及安全上限",
        "code": "app/douyin.py:discover_profile",
        "result": "完整展开作者所有历史已公开作品，供用户批量下载",
        "safety": "每次翻页加入微小随机抖动，减轻风控压力"
    },
    {
        "id": "DL-PL-023",
        "category": "平台接入与媒体逆向",
        "subsystem": "抖音与精选逆向",
        "name": "作者主页遭遇验证码阻断与诊断提示",
        "condition": "滚动主页过程中返回状态码包含验证码挑战（如 status_code==2098 或返回 verify 页面）",
        "logic": "捕获响应特征，立即抛出 AuthenticationRequiredError，标记 SiteIssueCode.VERIFICATION_REQUIRED",
        "code": "app/douyin.py:discover_profile",
        "result": "任务暂停并标黄，前端提示用户需完成安全验证",
        "safety": "立即终止后续翻页，防止 IP 被拉黑"
    },
    {
        "id": "DL-PL-024",
        "category": "平台接入与媒体逆向",
        "subsystem": "小红书逆向",
        "name": "xhslink.com / xhslink.cn 短链识别",
        "condition": "host 为 xhslink.com 或 xhslink.cn",
        "logic": "归纳为 Platform.XIAOHONGSHU，SourceKind.SHORT_LINK",
        "code": "app/platforms.py:identify_url:71",
        "result": "通过请求小红书短链重定向解析真实笔记页面",
        "safety": "域名白名单校验，防伪造短链"
    },
    {
        "id": "DL-PL-025",
        "category": "平台接入与媒体逆向",
        "subsystem": "小红书逆向",
        "name": "笔记详情与作者主页正则识别",
        "condition": "host 为 xiaohongshu.com 且 path 匹配 /user/profile/ 或 /(explore|discovery/item)/",
        "logic": "分别归类为 SourceKind.PROFILE 或 SourceKind.ITEM；其他不匹配路径抛出 UnsupportedUrlError",
        "code": "app/platforms.py:identify_url:76",
        "result": "精准分流单篇笔记与作者主页流程",
        "safety": "拦截小红书搜索页、群聊页等不支持格式"
    },
    {
        "id": "DL-PL-026",
        "category": "平台接入与媒体逆向",
        "subsystem": "小红书逆向",
        "name": "Direct CDN 原图地址优先还原",
        "condition": "解析小红书图文笔记时，存在多个分辨率图片地址",
        "logic": "从原始 JSON 提取 imageList，优先提取 urlDefault 或 direct CDN 原始大图，剥离动态截取与压缩参数",
        "code": "app/xiaohongshu.py",
        "result": "获取未经二压的最高清 WebP/JPG/HEIC 原图",
        "safety": "杜绝抓取预览小缩略图"
    },
    {
        "id": "DL-PL-027",
        "category": "平台接入与媒体逆向",
        "subsystem": "小红书逆向",
        "name": "originVideoKey 原始视频流优先选择",
        "condition": "笔记为视频类型（type=='video'），JSON 包含 originVideoKey 字段",
        "logic": "优先使用 originVideoKey 拼接原始最高清 CDN 地址；若不存在则回退至 media.stream / mediaV2 分辨率择优",
        "code": "app/xiaohongshu.py",
        "result": "直接下载未经平台压缩转码的高码率视频原片",
        "safety": "若 key 失效自动平滑降级至次优分发流"
    },
    {
        "id": "DL-PL-028",
        "category": "平台接入与媒体逆向",
        "subsystem": "小红书逆向",
        "name": "实况 LivePhoto HEIC 原图与 MP4 视频提取",
        "condition": "小红书笔记图片包含 livePhoto 标记（含有 livePhotoVideo 视频地址）",
        "logic": "将静态高保真图（HEIC/JPG）与 3 秒动态短视频同时加入解析清单，标记为 LIVE_PHOTO 类型",
        "code": "app/xiaohongshu.py",
        "result": "成对交付 HEIC 图片与 MP4 视频，保持拍摄瞬间动效",
        "safety": "任一部分下载失败触发针对性重试"
    },
    {
        "id": "DL-PL-029",
        "category": "平台接入与媒体逆向",
        "subsystem": "小红书逆向",
        "name": "小红书封面冒充视频防御",
        "condition": "网页端渲染时将封面图作为视频预览占位",
        "logic": "严格校验视频流的媒体类型与尺寸，若视频 URL 指向图片格式或 Content-Type 不符，拒绝作为视频下载",
        "code": "app/xiaohongshu.py",
        "result": "拦截伪视频，避免将封面图重命名为 .mp4 导致无法播放",
        "safety": "保证媒体类型与文件真实格式百分之百一致"
    },
    {
        "id": "DL-PL-030",
        "category": "平台接入与媒体逆向",
        "subsystem": "小红书逆向",
        "name": "小红书滑块风控识别与 Chrome 一键唤起",
        "condition": "请求小红书页面返回 404/403 页面，页面内含有滑块验证码挑战元素",
        "logic": "捕获特征并抛出带有验证标志的错误，前端展示“请完成滑块验证”，并提供一键唤醒本地 Chrome 打开该笔记的快捷按钮",
        "code": "app/errors.py, app/static/home.js",
        "result": "用户在 Chrome 完成滑块后，点击拾影的重试即可无缝继续",
        "safety": "避免自动化暴力破解触发更严厉封禁"
    },
    {
        "id": "DL-PL-031",
        "category": "平台接入与媒体逆向",
        "subsystem": "快手逆向",
        "name": "快手多元域名识别与归一化",
        "condition": "域名包含 kuaishou.com, v.kuaishou.com, m.gifshow.com, 或 *.m.chenzhongtech.com",
        "logic": "调用 source_identity 提取作品 ID 或作者 ID，识别为 Platform.KUAISHOU",
        "code": "app/platforms.py:identify_url:61",
        "result": "统一转换为快手标准化处理流",
        "safety": "不支持的快手内部未知路由抛 UnsupportedUrlError"
    },
    {
        "id": "DL-PL-032",
        "category": "平台接入与媒体逆向",
        "subsystem": "快手逆向",
        "name": "Playwright 被动监听无痕抓取",
        "condition": "快手作品包含复杂的客户端私有加密签名接口",
        "logic": "通过 Playwright 启动普通隔离浏览器实例，被动监听网络响应流，拦截 GraphQL/API 返回的解密数据包",
        "code": "app/kuaishou.py",
        "result": "无需逆向加密算法即可稳定获取视频原始直链",
        "safety": "完全模拟真实用户浏览行为，零被封风险"
    },
    {
        "id": "DL-PL-033",
        "category": "平台接入与媒体逆向",
        "subsystem": "快手逆向",
        "name": "HORIZONTAL_ATLAS 横版图集无损还原",
        "condition": "快手作品数据为 HORIZONTAL_ATLAS 类型（包含多张横版图片数组）",
        "logic": "提取全部高清大图 WebP 链接，按展示序列编号为 -001, -002，依次加入下载队列",
        "code": "app/kuaishou.py",
        "result": "完整下载整套横版图集，保持原始无水印画质",
        "safety": "单张损坏自动重试"
    },
    {
        "id": "DL-PL-034",
        "category": "平台接入与媒体逆向",
        "subsystem": "快手逆向",
        "name": "VERTICAL_ATLAS 竖版超长图切片保全",
        "condition": "快手作品为 VERTICAL_ATLAS 类型（平台将超长图切分为多达 16 片长条切片）",
        "logic": "按切片顺序保留全部 16 片原始切片，依次编号归档，不进行粗暴的有损拼接",
        "code": "app/kuaishou.py",
        "result": "完整保全所有切片像素，避免拼接导致的内存溢出或画质二次压缩",
        "safety": "严格校验 16 片切片的索引连续性"
    },
    {
        "id": "DL-PL-035",
        "category": "平台接入与媒体逆向",
        "subsystem": "快手逆向",
        "name": "快手作者主页深度与指数退避限制",
        "condition": "抓取快手作者全部作品列表",
        "logic": "设置最大 500 页 / 10,000 条的严格安全上限；遇到分页延迟增加时自动执行指数退避等待（1s -> 2s -> 4s）",
        "code": "app/kuaishou.py",
        "result": "平稳完成数千条作品的抓取，避免遭遇快手 IP 限制",
        "safety": "遇频限抛出 SiteIssueCode.RATE_LIMITED"
    },
    {
        "id": "DL-PL-036",
        "category": "平台接入与媒体逆向",
        "subsystem": "哔哩哔哩逆向",
        "name": "B站单视频与多 P 选集 (?p=1,2) 识别",
        "condition": "B站视频 URL 含有 ?p=1 或多P选集参数",
        "logic": "识别为 Platform.BILIBILI；若为多P视频，自动展开各分 P 列表，支持按指定 P 号或全部下载",
        "code": "app/platforms.py, app/engine/discovery.py",
        "result": "各分 P 独立命名，追加 [P01]、[P02] 标识，清晰直观",
        "safety": "多P列表中单个不可用不影响其他 P 正常下载"
    },
    {
        "id": "DL-PL-037",
        "category": "平台接入与媒体逆向",
        "subsystem": "哔哩哔哩逆向",
        "name": "B站合集、系列、播单 (/lists/) 批量解析",
        "condition": "URL 包含 /lists/, /favlist, /medialist/, /channel/, /list/ 路径",
        "logic": "识别为 SourceKind.PLAYLIST，通过 API 批量抓取播单下全部 BV 号及标题元数据",
        "code": "app/platforms.py:identify_url:134",
        "result": "批量加入下载任务队列，支持前端预览弹窗勾选",
        "safety": "自动去除播单中失效、失效或私密视频"
    },
    {
        "id": "DL-PL-038",
        "category": "平台接入与媒体逆向",
        "subsystem": "哔哩哔哩逆向",
        "name": "B站专栏文章与动态图文 (/opus/, /read/cv) 解析",
        "condition": "URL path 匹配 /(read/cv|opus/)",
        "logic": "调用 bilibili_opus.py，解析正文富文本、段落文本，提取全部插图高清原图，校验域名白名单",
        "code": "app/bilibili_opus.py",
        "result": "将专栏正文与全部插图完整下载归档，支持离线图文阅读",
        "safety": "仅接受 hdslb.com, biliimg.com, bilivideo.com 等受信域名"
    },
    {
        "id": "DL-PL-039",
        "category": "平台接入与媒体逆向",
        "subsystem": "哔哩哔哩逆向",
        "name": "B站音视频分离流无损 Remux 合成",
        "condition": "yt-dlp 抓取到最高画质（bestvideo）与最高音质（bestaudio）分离流",
        "logic": "驱动本地 FFmpeg 执行封装合并：ffmpeg -i video -i audio -c copy output.mp4",
        "code": "app/engine/transfer.py",
        "result": "瞬间完成合并，零 CPU 编码重压缩，保留 100% 原始视听品质",
        "safety": "合并后自动删除临时的音视频分离 .part 缓存"
    },
    {
        "id": "DL-PL-040",
        "category": "平台接入与媒体逆向",
        "subsystem": "YouTube 逆向",
        "name": "YouTube 常规/Shorts/频道/列表识别",
        "condition": "输入 youtu.be 或 youtube.com 链接",
        "logic": "判断是否含 list= 或 /playlist；判断是否为 /@user, /channel/；分流为 ITEM, PLAYLIST, PROFILE",
        "code": "app/platforms.py:identify_url:145",
        "result": "正确分流各形态下载模式",
        "safety": "过滤非法及恶意查询参数"
    },
    {
        "id": "DL-PL-041",
        "category": "平台接入与媒体逆向",
        "subsystem": "YouTube 逆向",
        "name": "集成 Deno Runtime 应对平台 JS 挑战",
        "condition": "YouTube 启用新版 n-sig 或解密挑战函数时",
        "logic": "调用内置的轻量化 Deno 安全沙箱执行 JS 解密代码，迅速计算出有效签名参数",
        "code": "app/engine/core.py",
        "result": "稳定提取 4K / 8K 最高清音视频流，避免 403 封锁",
        "safety": "沙箱隔离运行，禁止任意系统调用"
    },
    {
        "id": "DL-PL-042",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "视频号 HTTPS 分享短链合规性校验",
        "condition": "输入微信视频号链接",
        "logic": "严格校验 scheme==https, port in (None, 443), host in ('weixin.qq.com', 'channels.weixin.qq.com'), path.startswith('/sph/')",
        "code": "app/wechat.py:validate_link",
        "result": "通过返回标准 URL，不合法抛 ValueError('请输入微信视频号的 HTTPS 分享链接。')",
        "safety": "严禁非标端口与未知主机访问"
    },
    {
        "id": "DL-PL-043",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "腾讯元宝 Session 状态检测与有效性比对",
        "condition": "发起视频号解析前",
        "logic": "读取 data/yuanbao-cookie.txt 或 Chrome 本地会话，POST 请求元宝接口测试鉴权状态",
        "code": "app/wechat.py:profile_yuanbao",
        "result": "若返回 401/403 则提示'腾讯元宝登录态已失效或无权访问，请更新登录态后重试。'",
        "safety": "保护用户隐私，杜绝将失效会话提交至远程"
    },
    {
        "id": "DL-PL-044",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "Playwright 扫码登录独立小窗自动拉起",
        "condition": "用户未配置元宝 Cookie 或 Cookie 已过期",
        "logic": "自动唤醒专用小尺寸浏览器窗口打开 yuanbao.tencent.com，展示登录二维码，轮询监听登录成功事件",
        "code": "app/wechat.py",
        "result": "用户手机扫码后，系统自动提取最新 Cookie 并存入密钥库，自动关闭小窗",
        "safety": "最长 6 分钟超时守护；不残留后台浏览器实例"
    },
    {
        "id": "DL-PL-045",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "专用 Chrome 扩展一键复制登录态",
        "condition": "用户在日常使用的 Chrome 中登录了元宝",
        "logic": "提供 extension/yuanbao-login-copy 扩展，用户点击扩展图标即可一键提取加密 Cookie 并回传拾影",
        "code": "extension/yuanbao-login-copy",
        "result": "免去复杂的开发者工具 F12 抓包，零门槛完成授权",
        "safety": "扩展仅申请 yuanbao.tencent.com 单域名权限"
    },
    {
        "id": "DL-PL-046",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "Windows DPAPI 本地硬件级凭据加密存储",
        "condition": "保存元宝 Cookie 到 data/yuanbao-cookie.txt",
        "logic": "调用 Windows 内核 CryptProtectData 进行机密数据加密，仅当前 Windows 用户会话解密",
        "code": "app/wechat_secret.py",
        "result": "即使数据文件被拷贝到其他电脑也无法被解密查看",
        "safety": "杜绝明文凭证泄露"
    },
    {
        "id": "DL-PL-047",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "playable_url Token 与 exportId 严格换取",
        "condition": "元宝解析返回包含 token 与 eid 的 playable_url",
        "logic": "提取 Query 参数中的 generalToken 与 exportId，向微信官方 finder-preview 接口换取真实视频元数据",
        "code": "app/wechat.py:profile_yuanbao:187",
        "result": "成功获取完整 feedInfo 与 authorInfo",
        "safety": "若返回 errCode 则提示'微信未允许播放该作品，请检查链接和账号观看权限。'"
    },
    {
        "id": "DL-PL-048",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "微信返回 shortUri 与提交短链严格一致性比对",
        "condition": "微信接口返回作品详情 feedInfo",
        "logic": "比对 feedInfo.get('shortUri') 与用户输入的 share_code，若不匹配立即阻断",
        "code": "app/wechat.py:profile_yuanbao:204",
        "result": "抛出 WeChatUserError('微信返回的作品与所提供的链接不一致，已停止下载。')",
        "safety": "彻底防御微信跨作品缓存污染导致的串作品故障"
    },
    {
        "id": "DL-PL-049",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "视频号媒体流安全域名校验 (finder.video.qq.com)",
        "condition": "微信返回可用视频播放地址",
        "logic": "校验 scheme==https, port in (None, 443), host=='finder.video.qq.com'",
        "code": "app/wechat.py:_trusted_media_url",
        "result": "放行官方视频流；不符抛出'微信返回的视频地址未通过安全验证。'",
        "safety": "防止恶意 URL 重定向与中间人攻击"
    },
    {
        "id": "DL-PL-050",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "无符号 64 位 decodeKey 完整性比对",
        "condition": "视频详情返回多个 decodeKey / decode_key",
        "logic": "校验 key 为 1-20 位纯数字、数值 < 2^64、且所有字段中的 key 必须一致",
        "code": "app/wechat.py:media_selection:253",
        "result": "提取解密密钥；若不一致或格式错误抛'微信返回的视频解密信息不一致。'",
        "safety": "防止非法密钥导致解密失败或数据损坏"
    },
    {
        "id": "DL-PL-051",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "ISAAC64 头部 128KB 二进制流式动态解密",
        "condition": "下载视频号加密媒体流",
        "logic": "对前 131,072 字节 (128KB) 采用 ISAAC64 算法流式解密，后续字节直接明文追加写入临时文件",
        "code": "app/wechat_isaac.py",
        "result": "流式输出完整无损的标准 MP4 文件",
        "safety": "内存开销恒定，不支持大文件整片读入"
    },
    {
        "id": "DL-PL-052",
        "category": "平台接入与媒体逆向",
        "subsystem": "微信视频号逆向",
        "name": "无 Content-Length 分块流式落盘与流验证",
        "condition": "微信视频 CDN 响应头不提供 Content-Length 长度",
        "logic": "以 64KB 为分块持续迭代写入，直到 EOF；随后调用 FFprobe 探测流完整性与时长",
        "code": "app/wechat.py",
        "result": "验证通过后发布至下载目录；损坏则抛出异常清理残余文件",
        "safety": "杜绝空文件或未传输完毕文件误标完成"
    },

    # =========================================================================
    # 2. 下载引擎、文件管线与数据一致性 (Engine & File Pipeline)
    # =========================================================================
    {
        "id": "DL-ENG-001",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "临时目录与隔离",
        "name": "任务专属分段目录与隔离键隔离",
        "condition": "启动下载任务前",
        "logic": "根据 job_id 与 item_id 生成安全的目录名，或根据 hash(job+item+output_dir) 在 temporary_root 生成独立隔离区",
        "code": "app/engine/transfer.py:_ytdlp_parts_directory",
        "result": "各任务临时文件 .part 物理隔离，互不产生写锁冲突",
        "safety": "任务结束后自动安全递归删除空临时目录"
    },
    {
        "id": "DL-ENG-002",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "断点续传与网络传输",
        "name": "HTTP Range 支持探测与断点续传",
        "condition": "网络中断后重新恢复下载，且磁盘已存在部分 .part 文件",
        "logic": "发送 Range: bytes=X- 请求，若服务端返回 206 Partial Content 则从偏移量 X 继续追加写入",
        "code": "app/engine/transfer.py",
        "result": "避免从头重新下载已传输的大文件，大幅节省网络流量与时间",
        "safety": "严格校验 Content-Range 响应头与当前文件大小一致性"
    },
    {
        "id": "DL-ENG-003",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "断点续传与网络传输",
        "name": "HTTP 416 范围无法满足时清空重下",
        "condition": "断点续传时，远程文件已变更导致服务端返回 HTTP 416 Requested Range Not Satisfiable",
        "logic": "捕获 416 状态码，清空已有的 .part 临时文件，自动重置传输游标为 0，发起常规 GET 全量请求",
        "code": "app/engine/transfer.py",
        "result": "自动自愈坏块，避免任务陷入 416 死循环",
        "safety": "记录告警日志并重置进度指标"
    },
    {
        "id": "DL-ENG-004",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "格式判定与魔数嗅探",
        "name": "真实二进制文件头魔数嗅探 (Magic Number)",
        "condition": "媒体文件下载完毕或获取首包字节（first_bytes）时",
        "logic": "根据前几个字节判断格式：\\xff\\xd8\\xff (JPG), \\x89PNG (PNG), GIF87a/89a (GIF), RIFF...WEBP (WebP), ftyp (MP4/HEIC/AVIF)",
        "code": "app/engine/naming.py:_asset_extension:133",
        "result": "准确赋予正确扩展名，不盲目信赖 URL 伪造后缀",
        "safety": "防止 Content-Type 与实际内容不一致导致打不开"
    },
    {
        "id": "DL-ENG-005",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "格式判定与魔数嗅探",
        "name": "ISO 媒体品牌 (ftyp box) 细化判别",
        "condition": "文件头包含 ftyp box，需要区分 MP4, HEIC, AVIF 等",
        "logic": "解析 box_size 与 brands 集合：若含 avif/avis 则为 .avif；若含 heic/heix/hevc 则为 .heic；若为视频则为 .mp4",
        "code": "app/engine/naming.py:_asset_extension:141",
        "result": "精准区分现代高保真图片格式与视频格式",
        "safety": "模糊 ISO 图（mif1/msf1）结合 URL 补充判断，杜绝误判"
    },
    {
        "id": "DL-ENG-006",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "本地媒体解码验证",
        "name": "FFmpeg 全量像素无损解码深度校验",
        "condition": "本地复用或下载完成图片文件后",
        "logic": "启动 ffmpeg -v error -xerror -err_detect explode -i <path> -f null - 完整解码每个像素",
        "code": "app/engine/transfer.py:_decode_local_image:94",
        "result": "若存在坏道、文件截断或损坏，即刻抛出异常强制重下",
        "safety": "绝不把看似正常的坏图当做成功成员保留"
    },
    {
        "id": "DL-ENG-007",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "文件原子发布",
        "name": "publish_no_replace 跨卷原子重命名与硬链接",
        "condition": "将验证通过的临时文件发布到最终输出路径",
        "logic": "优先使用 os.link() 硬链接；若在 Windows 下跨目录同卷则使用原子的 os.rename()；目标存在抛 FileExistsError",
        "code": "app/file_publish.py:publish_no_replace:11",
        "result": "瞬间完成发布，且绝对不会覆盖已有同名文件",
        "safety": "避免文件写一半被用户或播放器读取"
    },
    {
        "id": "DL-ENG-008",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "文件原子发布",
        "name": "publish_unique 重名追加 (2), (3) 递增保护",
        "condition": "目标路径已存在用户原有同名文件，且内容不一致",
        "logic": "循环尝试 {stem} ({number}){suffix}，从 2 递增至 10001，直到找到未占用的文件名并原子发布",
        "code": "app/file_publish.py:publish_unique:26",
        "result": "安全发布新文件，完美保全用户原有旧资产",
        "safety": "最大尝试 10001 次防死锁循环"
    },
    {
        "id": "DL-ENG-009",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "文件原子发布",
        "name": "are_files_identical 快速首尾哈希对比",
        "condition": "发现已有同名文件，需判定是否为同一文件",
        "logic": "拒绝软链接；比对 stat 文件大小；≤1MB 逐字节对比；>1MB 对比首尾 64KB (65536 bytes) 数据块",
        "code": "app/file_publish.py:are_files_identical:40",
        "result": "快速、确定性判定两文件内容是否完全一致，耗时 < 5ms",
        "safety": "无需全盘计算大文件哈希，大幅降低磁盘 I/O"
    },
    {
        "id": "DL-ENG-010",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "文件原子发布",
        "name": "publish_or_reuse_identical 相同内容智能复用",
        "condition": "下载前或下载后，目标目录已存在完全相同内容的文件",
        "logic": "排查目标文件名及 (2)...(99) 候选，若命中 identical 则删除临时文件直接返回已存在文件路径",
        "code": "app/file_publish.py:publish_or_reuse_identical:63",
        "result": "实现秒级“极速完成”，避免重复生成冗余文件",
        "safety": "若内容不同自动回退至 publish_unique"
    },
    {
        "id": "DL-ENG-011",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "文件原子发布",
        "name": "publish_pair_image 实况照片基名同步独占锁",
        "condition": "发布 Live Photo 的静态图与实况短视频",
        "logic": "加全局 _PAIR_NAME_LOCK 锁；扫描目录下已占用 stem；找到同时未被图片和视频占用的 stem 并原子发布",
        "code": "app/file_publish.py:publish_pair_image:79",
        "result": "确保实况照片的 .webp/.jpg 与 .mp4 主文件名完全一致",
        "safety": "多线程并发写入时绝不出现图和视频序号错位"
    },
    {
        "id": "DL-ENG-012",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "文件命名与路径规整",
        "name": "Windows 保留字符与非法字符安全清洗",
        "condition": "作品标题包含 Windows 系统保留字符 \\ / : * ? \" < > |",
        "logic": "调用 safe_component，将非法字符替换为合法下划线或全角字符，清理前后空格与句点",
        "code": "app/engine/naming.py",
        "result": "生成在 Windows/macOS/Linux 均合法的有效文件名",
        "safety": "防止由于路径非法字符触发系统 OSError 崩溃"
    },
    {
        "id": "DL-ENG-013",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "文件命名与路径规整",
        "name": "超长标题智能截断与安全回退",
        "condition": "作品标题长度超过配置的 filename_limit（如超过 80 字符）",
        "logic": "智能截断标题文字，确保加上日期、作者与 [media_id] 后整体路径不超系统 260 字符上限；全空回退为 media_id",
        "code": "app/engine/naming.py",
        "result": "既保留标题辨识度，又符合操作系统长路径规范",
        "safety": "杜绝 Windows MAX_PATH 溢出错误"
    },
    {
        "id": "DL-ENG-014",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "文件命名与路径规整",
        "name": "快手紧凑命名与稳定 ID 冲突防御",
        "condition": "快手作品归档命名",
        "logic": "默认生成 {date}-{title}{suffix}；若存在冲突自动追加 [{media_id}]；再冲突追加 [{media_id}-{counter}]",
        "code": "app/engine/naming.py:_xhs_output_path:112",
        "result": "保持人类可读优先，碰撞时稳定兜底",
        "safety": "绝不覆盖已有资产"
    },
    {
        "id": "DL-ENG-015",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "多媒体归档结构",
        "name": "按平台/作者两级层级树状自动归档",
        "condition": "开启 organized_output（系统默认）",
        "logic": "按 downloads/{Platform}/{Author}/ 自动创建父文件夹，单作品归档于其中；合集作品归入对应子专辑文件夹",
        "code": "app/manager/core.py",
        "result": "文件井井有条，支持百千作品清晰归类管理",
        "safety": "目录不存在时自动 mkdir(parents=True, exist_ok=True)"
    },

    # =========================================================================
    # 3. 任务调度、状态机与生命周期 (Task Management & Lifecycle)
    # =========================================================================
    {
        "id": "DL-MGR-001",
        "category": "任务调度与生命周期管理",
        "subsystem": "并发控制与队列",
        "name": "全局任务并发槽位与排队控制",
        "condition": "用户同时添加多个单作品或主页任务",
        "logic": "初始化内部并发控制信号量，活跃下载任务数严格受限，超出阈值的任务自动进入 QUEUED 状态排队",
        "code": "app/manager/core.py",
        "result": "平稳消耗系统网络与 CPU 资源，防止大流量被目标平台封 IP",
        "safety": "前序任务完成后自动唤醒后续排队任务"
    },
    {
        "id": "DL-MGR-002",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "QUEUED -> DISCOVERING 状态变迁",
        "condition": "任务出队开始执行解析",
        "logic": "状态由 QUEUED 变更为 DISCOVERING，通过 SSE 向前端推送任务更新事件",
        "code": "app/manager/lifecycle.py",
        "result": "前端展示蓝色“解析中”胶囊徽标",
        "safety": "若解析失败直接跳至 FAILED 并记录错误详情"
    },
    {
        "id": "DL-MGR-003",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "DISCOVERING -> DOWNLOADING 状态变迁",
        "condition": "链接探测与选集解析完毕，开始发起媒体流下载",
        "logic": "状态变迁为 DOWNLOADING，初始化进度传输对象 TransferProgress",
        "code": "app/manager/lifecycle.py",
        "result": "前端展示动态传输速度 (MB/s)、已下字节数与百分比进度条",
        "safety": "支持用户随时点击暂停或取消"
    },
    {
        "id": "DL-MGR-004",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "全部成功 -> COMPLETED 状态变迁",
        "condition": "任务包含的所有 DownloadItem 均已成功完成并发布",
        "logic": "任务状态变迁为 COMPLETED，清空当前速度指标，触发完成事件广播",
        "code": "app/manager/lifecycle.py",
        "result": "前端展示绿色“已完成”徽标，点亮“播放”与“定位目录”按钮",
        "safety": "自动安全释放所有临时持有的锁与缓存句柄"
    },
    {
        "id": "DL-MGR-005",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "多项部分成功 -> PARTIAL 状态变迁",
        "condition": "合集或图集任务中，部分项目成功，部分项目失败或遇风控",
        "logic": "状态标为 PARTIAL，保留已完成项目的输出路径，未完成项目保留各自失败原因",
        "code": "app/manager/lifecycle.py",
        "result": "前端展示橙色“部分完成”，提供“重试未完成项”快捷操作",
        "safety": "已成功项目绝不重复下载"
    },
    {
        "id": "DL-MGR-006",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "任务失败 -> FAILED 状态变迁与错误码绑定",
        "condition": "解析或下载过程中发生不可恢复异常",
        "logic": "调用 classify_site_issue 分类错误，将 job.status 设为 FAILED，写入 issue_code 与 detail",
        "code": "app/manager/lifecycle.py, app/errors.py",
        "result": "前端展示红色“下载失败”，并根据 issue_code 显示中文诊断指引",
        "safety": "捕获所有异常，保证调度器本身不崩溃"
    },
    {
        "id": "DL-MGR-007",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "凭证失效 -> NEEDS_AUTH 状态变迁",
        "condition": "遇到强制登录或 Cookie 失效错误（分类为 LOGIN_REQUIRED / COOKIE_UNAVAILABLE）",
        "logic": "状态变更为 NEEDS_AUTH，置顶展示，提醒用户需要授权",
        "code": "app/manager/lifecycle.py",
        "result": "前端按钮变为“更新凭据”或“扫码登录”，引导用户解决凭证问题",
        "safety": "停止重试，避免被对端封禁账号"
    },
    {
        "id": "DL-MGR-008",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "用户主动暂停 -> PAUSED 状态流转",
        "condition": "用户点击任务卡片上的“暂停”按钮",
        "logic": "向该任务的下载线程发出 should_cancel() 信号，保留已写入的 .part 临时文件，状态置为 PAUSED",
        "code": "app/manager/lifecycle.py:pause_job",
        "result": "传输平稳停止，前端展示黄色“已暂停”徽标与“继续”按钮",
        "safety": "保留断点续传临时数据"
    },
    {
        "id": "DL-MGR-009",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "已暂停任务恢复 -> RESUME 流程",
        "condition": "用户在 PAUSED 任务上点击“继续”",
        "logic": "重新加入下载队列，读取现存 .part 文件发起 Range 断点续传，状态切换回 DOWNLOADING",
        "code": "app/manager/lifecycle.py:resume_job",
        "result": "从上次暂停的字节位置继续下载，进度无缝衔接",
        "safety": "若远程资源已失效则自动提示刷新"
    },
    {
        "id": "DL-MGR-010",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "用户取消 -> CANCELLED 流程与缓存保留",
        "condition": "用户点击“取消”按钮",
        "logic": "向线程发出取消令牌，杀死子进程（如 FFmpeg），状态流转为 CANCELLED",
        "code": "app/manager/lifecycle.py:cancel_job",
        "result": "任务终止，前端显示“已取消”，支持一键重新发起",
        "safety": "优雅关闭未尽 I/O"
    },
    {
        "id": "DL-MGR-011",
        "category": "任务调度与生命周期管理",
        "subsystem": "10种状态机流转",
        "name": "进程退出未完成 -> INTERRUPTED 启动自愈",
        "condition": "程序异常崩溃、断电或被强制杀死，重启读取持久化状态",
        "logic": "启动时检测处于 queued / downloading / capturing 的任务，自动将其收敛为 INTERRUPTED",
        "code": "app/main.py, app/wechat.py:112",
        "result": "提示'上次运行已中断，请重新提交链接'，避免任务永久卡死在下载中",
        "safety": "保障系统启动状态干净稳定"
    },
    {
        "id": "DL-MGR-012",
        "category": "任务调度与生命周期管理",
        "subsystem": "重试调度",
        "name": "单项针对性重试 (Retry Single Item)",
        "condition": "用户在多选集或图集中点击某个具体失败项的“重试”",
        "logic": "POST /api/jobs/{id}/retry 传入 item_id，仅将该 item 状态重置为 QUEUED，其他已完成项保持原样不动",
        "code": "app/manager/core.py:retry_job",
        "result": "仅重新探测并下载该单项，节省大量时间与网络开销",
        "safety": "若该 item 正在下载报 HTTP 409 JobBusyError"
    },
    {
        "id": "DL-MGR-013",
        "category": "任务调度与生命周期管理",
        "subsystem": "重试调度",
        "name": "全部失败项一键重试 (Retry All Failed)",
        "condition": "用户点击任务卡片上的主“重试”按钮（不带 item_id）",
        "logic": "批量筛选 job.items 中所有处于 FAILED, NEEDS_AUTH, CANCELLED 状态的项，统一重置为 QUEUED 触发调度",
        "code": "app/manager/core.py:retry_job",
        "result": "批量并发启动所有未成功项，已完成项完全不受影响",
        "safety": "若无任何可重试项，返回友好提示"
    },
    {
        "id": "DL-MGR-014",
        "category": "任务调度与生命周期管理",
        "subsystem": "删除与清理",
        "name": "安全删除任务记录（保留本地物理文件）",
        "condition": "用户在任务卡片上点击“删除任务”图标",
        "logic": "调用 DELETE /api/jobs/{id}，从内存及持久化 state_dir 中彻底注销任务，但绝不删除 downloads/ 下已下载好的用户文件",
        "code": "app/manager/core.py:delete_job",
        "result": "任务卡片从前端视窗消失，用户已下载资产完好无损",
        "safety": "防止误操作造成不可挽回的资产丢失"
    },
    {
        "id": "DL-MGR-015",
        "category": "任务调度与生命周期管理",
        "subsystem": "持久化容灾",
        "name": "任务元数据原子写入与损坏自愈",
        "condition": "任务状态变迁需要持久化保存到磁盘",
        "logic": "写入临时 .tmp 文件，使用 handle.flush() 与 os.fsync 刷盘，再原子替换正式 .json 状态文件；若读取损坏自动备份并重置",
        "code": "app/manager/core.py",
        "result": "确保状态文件零损坏率，异常断电不丢数据",
        "safety": "多线程 RLock 互斥锁保护"
    },

    # =========================================================================
    # 4. RESTful API 契约与网络安全 (RESTful API & Security Middleware)
    # =========================================================================
    {
        "id": "DL-API-001",
        "category": "RESTful API与网络通信",
        "subsystem": "安全中间件",
        "name": "TrustedHostMiddleware 主机名白名单防御",
        "condition": "客户端发送带有恶意或伪造 Host 头的 HTTP 请求",
        "logic": "仅放行 127.0.0.1, localhost 及当前服务端口，其余 Host 直接返回 HTTP 400",
        "code": "app/main.py",
        "result": "阻断 DNS 重绑定攻击 (DNS Rebinding Attack)",
        "safety": "保护本地客户端不受恶意公网网页利用"
    },
    {
        "id": "DL-API-002",
        "category": "RESTful API与网络通信",
        "subsystem": "安全中间件",
        "name": "local_mutations_only 跨站变更严格拒绝 (CSRF 防御)",
        "condition": "外部网页发起跨站请求伪造 POST/PUT/DELETE 修改任务或配置",
        "logic": "检查非 GET/HEAD/OPTIONS 请求：若 Sec-Fetch-Site == 'cross-site' 或 Origin 不等于本机 base_url，返回 HTTP 403",
        "code": "app/main.py:local_mutations_only:202",
        "result": "返回 403 {'detail': '只接受本机拾影页面的操作。'}",
        "safety": "彻底杜绝用户浏览恶意网页时静默操作本地下载器"
    },
    {
        "id": "DL-API-003",
        "category": "RESTful API与网络通信",
        "subsystem": "安全中间件",
        "name": "disable_runtime_asset_cache 静态资源防脏读",
        "condition": "请求根路径 / 或 /static/* 静态资源",
        "logic": "强制注入响应头 Cache-Control: no-store, max-age=0 与 Pragma: no-cache",
        "code": "app/main.py:disable_runtime_asset_cache:317",
        "result": "保证 pywebview 桌面壳与浏览器每次打开均加载最新 UI 代码",
        "safety": "杜绝热更新后旧 JS 缓存导致的界面逻辑不一致"
    },
    {
        "id": "DL-API-004",
        "category": "RESTful API与网络通信",
        "subsystem": "路径边界防护",
        "name": "_resolve_within 目录遍历与路径穿越拦截",
        "condition": "通过 API 请求打开特定目录或文件",
        "logic": "解析目标路径为绝对物理路径，使用 path.relative_to(root) 严格校验是否在任务合法的 output roots 内",
        "code": "app/main.py:_resolve_within:220",
        "result": "若越界，立即抛出 HTTPException(400, '该目录不在任务的保存范围内，已拒绝打开。')",
        "safety": "防止攻击者利用 ../ 打开敏感系统文件夹"
    },
    {
        "id": "DL-API-005",
        "category": "RESTful API与网络通信",
        "subsystem": "配置管理容灾",
        "name": "config.json 损坏自愈与时间戳备份",
        "condition": "启动读取 config.json 时发生 JSON 解析异常或语法截断",
        "logic": "捕获异常，将损坏文件原子重命名为 config.corrupt-YYYYMMDD-HHMMSS.json，内存回退至默认配置，激活 CONFIG_RECOVERY 告警",
        "code": "app/main.py:_load_config:124",
        "result": "系统平稳启动不闪退，前端展示黄色通知告知用户已备份并使用默认设置",
        "safety": "保护用户原文件现场，便于排查恢复"
    },
    {
        "id": "DL-API-006",
        "category": "RESTful API与网络通信",
        "subsystem": "配置管理容灾",
        "name": "_save_config 临时文件原子替换与刷盘",
        "condition": "用户在设置抽屉修改保存目录或 Cookie 设置",
        "logic": "加 _CONFIG_LOCK 锁；创建临时文件写入并调用 fsync 刷盘；调用 Path.replace 原子覆盖正式配置文件",
        "code": "app/main.py:_save_config:146",
        "result": "确保磁盘配置写入具备 ACID 事务性，永不生成半拉子文件",
        "safety": "写入失败时在 finally 中安全删除临时文件"
    },
    {
        "id": "DL-API-007",
        "category": "RESTful API与网络通信",
        "subsystem": "实时事件推送",
        "name": "GET /api/events SSE 实时状态流订阅",
        "condition": "前端页面加载建立 Server-Sent Events 连接",
        "logic": "保持长连接，每当任务状态、百分比、速度或错误发生变化时，服务端即时向客户端推流 JSON 数据帧",
        "code": "app/main.py:event_stream",
        "result": "前端无须低效短轮询，实现毫秒级平滑刷新",
        "safety": "客户端断开时自动清理服务端推送队列，无内存泄露"
    },
    {
        "id": "DL-API-008",
        "category": "RESTful API与网络通信",
        "subsystem": "安全停机控制",
        "name": "POST /api/runtime/stop 令牌鉴权安全停机",
        "condition": "用户点击退出桌面应用，或通过 launcher/stop.py 发起停机",
        "logic": "请求头必须携带与进程启动时一致的 X-Stop-Token 密钥，校验通过后向主循环发出停止信号并优雅退出",
        "code": "app/main.py",
        "result": "无 token 报 403 拒绝；合法则返回 {'status': 'stopping'} 并执行平稳清理",
        "safety": "防止第三方网页或局域网设备随意关闭后台进程"
    },

    # =========================================================================
    # 5. 桌面客户端集成、进程守护与操作系统桥接 (Desktop & Process Guard)
    # =========================================================================
    {
        "id": "DL-DSK-001",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "进程树管控",
        "name": "Win32 Job Object 级联进程树绑定守护",
        "condition": "启动主服务时（launcher.py / process_guard.py）",
        "logic": "创建 Win32 作业对象，设置 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 标志，将主进程及所有派生的 Playwright/FFmpeg 子进程纳入其中",
        "code": "launcher.py, process_guard.py",
        "result": "主进程因崩溃、任务管理器结束或断电退出时，Windows 内核强制同步瞬间杀死全部子进程",
        "safety": "绝对零孤儿进程残留，杜绝内存与 CPU 偷跑"
    },
    {
        "id": "DL-DSK-002",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "端口与单实例",
        "name": "8766 端口智能复用与全局互斥锁 (Mutex)",
        "condition": "用户重复双击启动拾影桌面应用",
        "logic": "创建命名互斥体 Shiying_SingleInstance_Mutex；检测 8766 端口，若已有健康实例在运行，直接唤醒置顶已有窗口并退出新进程",
        "code": "launcher.py",
        "result": "保证单机永远只运行单个服务实例，避免端口冲突与数据竞争",
        "safety": "零多重实例打架风险"
    },
    {
        "id": "DL-DSK-003",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "Windows 资源管理器置顶",
        "name": "Python.NET Win32 STA COM 调度资源管理器窗口置顶",
        "condition": "用户点击“打开文件夹”或“定位文件所在位置”",
        "logic": "在 STA 线程中通过 clr 调用 Shell.Application COM 对象，枚举已打开的窗口，比对 LocationURL；匹配则调用 SetForegroundWindow 唤醒置顶",
        "code": "app/desktop.py:open_folder_foreground:73",
        "result": "解决 Windows 资源管理器打开目录总被埋在后台的痛点，精准弹到用户视线最前方",
        "safety": "若最小化则先调用 ShowWindow(handle, 9) 恢复"
    },
    {
        "id": "DL-DSK-004",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "Windows 资源管理器置顶",
        "name": "无匹配窗口时启动 explorer.exe 并循环等待捕获置顶",
        "condition": "目标文件夹尚未在任何资源管理器窗口中打开",
        "logic": "启动 subprocess.Popen(['explorer.exe', target])，在超时循环中等待该窗口句柄生成，捕获后将其置顶",
        "code": "app/desktop.py:open_folder_foreground",
        "result": "顺滑打开新窗口并保持前台聚焦",
        "safety": "超时自动退出，不产生死循环"
    },
    {
        "id": "DL-DSK-005",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "多媒体安全打开",
        "name": "OPENABLE_MEDIA_SUFFIXES 白名单严格执行防投毒",
        "condition": "用户点击“直接播放”或打开文件",
        "logic": "校验 path.suffix.lower() 必须属于预设的 23 种音视频和图片后缀之一；非白名单直接拒绝执行",
        "code": "app/desktop.py:can_open_saved_media:20",
        "result": "合法文件调用系统默认播放器打开；非法类型抛出 ValueError('此文件类型不能直接打开，请在文件夹中查看。')",
        "safety": "彻底防御下载到恶意 .exe, .bat, .vbs, .lnk 等脚本被诱导执行"
    },
    {
        "id": "DL-DSK-006",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "文件定位",
        "name": "系统文件管理器高亮选中指定文件",
        "condition": "点击文件卡片上的“定位”按钮（reveal=True）",
        "logic": "Windows 下执行 explorer.exe /select, <path>；macOS 下执行 open -R <path>；Linux 下打开父目录",
        "code": "app/desktop.py:open_saved_file:56",
        "result": "打开所在文件夹并在窗口中自动选中高亮目标文件",
        "safety": "若文件已被物理删除，报 404 '文件已移动或删除，下载记录仍然保留。'"
    },
    {
        "id": "DL-DSK-007",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "环境依赖探针",
        "name": "FFmpeg / FFprobe / Chrome 注册表与路径综合探测",
        "condition": "页面加载或访问 GET /api/environment",
        "logic": "shutil.which 探测 PATH；Windows 下遍历注册表 HKCU/HKLM 检索 Chrome 真实安装路径；检测下载目录可写权限",
        "code": "app/environment.py:check_environment",
        "result": "输出包含 6 大组件健康状态的结构化自检字典",
        "safety": "缺依赖时在前端展示明确的下载安装引导"
    },

    # =========================================================================
    # 6. 前端交互与视窗响应全场景状态机 (Frontend & UI State Machine)
    # =========================================================================
    {
        "id": "DL-UI-001",
        "category": "前端交互与现代UI系统",
        "subsystem": "智能输入与剪贴板",
        "name": "粘贴杂乱分享文案时自动提取并清洗 URL",
        "condition": "用户将手机 App 复制的“【某某作者】分享了视频... https://v.douyin.com/... 复制此链接”整段粘贴到输入框",
        "logic": "输入框失焦或点击提交时，调用前端与后端正则自动剥离杂文，仅保留核心 URL",
        "code": "app/static/home.js",
        "result": "输入框自动更新为纯净链接，直接进入解析",
        "safety": "用户免去手工删减文字的繁琐操作"
    },
    {
        "id": "DL-UI-002",
        "category": "前端交互与现代UI系统",
        "subsystem": "预览弹窗与选集",
        "name": "多P选集 / 图集列表解析预览弹窗 (Preview Dialog)",
        "condition": "用户点击“解析预览”或提交了合集/主页链接",
        "logic": "弹出全屏/模态预览框，异步拉取所有单项封面、标题、清晰度与媒体类型，支持全选、反选、单独勾选",
        "code": "app/static/home.js",
        "result": "用户自主勾选所需作品，点击“开始下载”批量入队",
        "safety": "超时时间设为 240 秒，给主页扫描预留充足时间"
    },
    {
        "id": "DL-UI-003",
        "category": "前端交互与现代UI系统",
        "subsystem": "重复任务防误触",
        "name": "重复任务提交前置校验与交互弹窗",
        "condition": "用户输入了一个已经存在于任务列表中的 URL（force=False）",
        "logic": "先调用 GET /api/jobs/exists，若已存在，弹出提示弹窗：“该链接已存在于任务列表中”",
        "code": "app/static/home.js",
        "result": "提供两个按钮：“查看已有任务”（滚动并高亮该卡片）和“仍然下载”（带 force=True 重新提交）",
        "safety": "有效防止误粘贴导致的重复无效下载"
    },
    {
        "id": "DL-UI-004",
        "category": "前端交互与现代UI系统",
        "subsystem": "任务视窗分类与搜索",
        "name": "分类 Tab 筛选（全部/下载中/已完成/需处理/已暂停）",
        "condition": "用户点击顶部不同状态 Tab",
        "logic": "基于 state.filter 动态计算可视任务集合，瞬时切换 DOM 节点可见性",
        "code": "app/static/home.js",
        "result": "海量任务时依然能毫秒级定位特定状态任务",
        "safety": "Tab 切换保持当前滚动位置与搜索关键词"
    },
    {
        "id": "DL-UI-005",
        "category": "前端交互与现代UI系统",
        "subsystem": "任务视窗分类与搜索",
        "name": "实时关键词模糊搜索与拼音匹配",
        "condition": "在搜索框输入文字",
        "logic": "监听 input 事件，对标题（title）、作者（author）、作品ID（media_id）及 URL 进行大小写不敏感匹配",
        "code": "app/static/home.js",
        "result": "任务卡片列表即时收缩至匹配项，清空时秒恢复",
        "safety": "零后端请求开销，纯客户端内存过滤"
    },
    {
        "id": "DL-UI-006",
        "category": "前端交互与现代UI系统",
        "subsystem": "快捷入口与引导",
        "name": "7大平台图标快捷筛选与点击填入示例",
        "condition": "点击输入框上方的平台 Logo（抖音、快手、小红书等）",
        "logic": "若当前无输入，自动填入该平台支持的典型格式说明与占位符；若在任务列表页，快速按该平台过滤任务",
        "code": "app/static/home.js",
        "result": "极大提升多平台混合管理时的切换效率",
        "safety": "平台对应专属高清矢量图标渲染"
    },
    {
        "id": "DL-UI-007",
        "category": "前端交互与现代UI系统",
        "subsystem": "内置轻量播放器",
        "name": "内置媒体播放弹窗与图片画廊",
        "condition": "已完成任务卡片上点击“播放”按钮",
        "logic": "如果为视频则拉起内置 HTML5 播放器弹窗；如果为图集则启动幻灯片画廊，支持上一张/下一张切换",
        "code": "app/static/home.js",
        "result": "用户无需跳出拾影即可立即审阅下载质量",
        "safety": "流式加载，支持空格暂停与 Esc 快速关闭"
    },
    {
        "id": "DL-UI-008",
        "category": "前端交互与现代UI系统",
        "subsystem": "设置与个性化",
        "name": "设置抽屉与深色/浅色主题即时切换",
        "condition": "点击右上角“齿轮”图标打开设置抽屉，切换主题模式",
        "logic": "修改 document.documentElement 的 data-theme 属性（light / dark / auto），并持久化到 localStorage",
        "code": "app/static/home.js, app/static/home.css",
        "result": "界面色调无缝瞬间切换，夜间使用柔和护眼",
        "safety": "遵循系统偏好，零布局跳动"
    },
    {
        "id": "DL-UI-009",
        "category": "前端交互与现代UI系统",
        "subsystem": "网络连接状态指示灯",
        "name": "后端心跳失联红色告警与自动重连",
        "condition": "后端进程关闭或网络中断导致 SSE / API 失联",
        "logic": "右下角 connection 胶囊变为红色'连接中断'，禁用提交按钮；后台以指数退避自动重试握手，成功后瞬间复原为绿色'本地运行'",
        "code": "app/static/home.js:connection:111",
        "result": "用户清晰感知系统运行状态，网络自愈后自动恢复交互",
        "safety": "防止失联期间盲目提交引发未定义错误"
    },

    # =========================================================================
    # 7. 全系统异常诊断、风控应对与安全沙箱 (Risk & Security Sandbox)
    # =========================================================================
    {
        "id": "DL-SEC-001",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "15大类SiteIssueCode",
        "name": "RATE_LIMITED 触发判定与指数退避指引",
        "condition": "响应包含 429, 'too many requests', 'rate-limit', '访问过于频繁', '操作频繁' 等",
        "logic": "分类为 SiteIssueCode.RATE_LIMITED，前端展示'访问过于频繁，平台已实施临时限频，请等待几分钟后再试'",
        "code": "app/errors.py:30, app/manager/issues.py",
        "result": "暂停高频重试，引导用户合理降频",
        "safety": "防止 IP 遭受平台封禁惩罚"
    },
    {
        "id": "DL-SEC-002",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "15大类SiteIssueCode",
        "name": "VERIFICATION_REQUIRED 滑块挑战与人机验证",
        "condition": "响应包含 'captcha challenge', 'verify you are human', '请完成验证码', '请拖动滑块' 等，且非声明不需验证",
        "logic": "分类为 SiteIssueCode.VERIFICATION_REQUIRED，提取 challenge 链接，提供一键打开浏览器验证入口",
        "code": "app/errors.py:72, app/manager/issues.py",
        "result": "任务卡片展示橙色验证警示，附带直达验证按钮",
        "safety": "人工过盾后点击重试即可恢复"
    },
    {
        "id": "DL-SEC-003",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "15大类SiteIssueCode",
        "name": "LOGIN_REQUIRED 需登录与私密作品拦截",
        "condition": "响应包含 'login required', 'please log in', 'session expired', '请先登录', 'user account is private' 等",
        "logic": "分类为 SiteIssueCode.LOGIN_REQUIRED，指引用户在设置中开启 Chrome Cookie 或完成扫码登录",
        "code": "app/errors.py:162, app/manager/issues.py",
        "result": "任务标为需要登录，提供 Cookie 授权引导入口",
        "safety": "明确告知用户权限边界，非系统程序 Bug"
    },
    {
        "id": "DL-SEC-004",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "15大类SiteIssueCode",
        "name": "COOKIE_UNAVAILABLE 本地 Cookie 占用与失效处理",
        "condition": "读取 Chrome Cookie 时遇到文件占用锁定、解密失败或元宝 Cookie 过期",
        "logic": "分类为 SiteIssueCode.COOKIE_UNAVAILABLE，明确提示'无法读取 Chrome Cookie，请完全退出 Chrome 后重试，或手动配置'",
        "code": "app/errors.py:126, app/manager/issues.py",
        "result": "精准定位本地环境冲突，给出清晰排查步骤",
        "safety": "绝不向界面泄露残缺的 Cookie 明文"
    },
    {
        "id": "DL-SEC-005",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "15大类SiteIssueCode",
        "name": "CONTENT_UNAVAILABLE 作品被删或不可见判定",
        "condition": "返回 404, 410, 'video unavailable', 'has been removed', '不存在或当前不可见', '已删除'",
        "logic": "分类为 SiteIssueCode.CONTENT_UNAVAILABLE，标识该资源已从源站下架，停止无意义的反复重试",
        "code": "app/errors.py:241, app/manager/issues.py",
        "result": "任务标记失败，提示'原作品已被作者删除或设置为仅自己可见'",
        "safety": "释放并发队列槽位"
    },
    {
        "id": "DL-SEC-006",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "15大类SiteIssueCode",
        "name": "SITE_RESPONSE_CHANGED 数据结构变更与防串号报警",
        "condition": "检测到返回 awemeId 不一致、不同作品、different author、cross-wired、返回空内容或解析字段缺失",
        "logic": "分类为 SiteIssueCode.SITE_RESPONSE_CHANGED，阻断下载流程并记录结构变动诊断日志",
        "code": "app/errors.py:279, app/manager/issues.py",
        "result": "防止将错误内容存入用户电脑，提示'平台返回数据异常，已停止下载'",
        "safety": "保护数据完整性与正确性"
    },
    {
        "id": "DL-SEC-007",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "15大类SiteIssueCode",
        "name": "SECURITY_BLOCKED 非法跳转与 SSRF 防护触发",
        "condition": "检测到 untrusted url, outside the trusted, nonstandard-port, redirect could not be trusted 等",
        "logic": "分类为 SiteIssueCode.SECURITY_BLOCKED，拒绝连接目标地址，作为高危安全事件拦截",
        "code": "app/errors.py:262, app/manager/issues.py",
        "result": "严格阻断潜在的 SSRF、端口扫描或钓鱼跳转攻击",
        "safety": "系统核心安全防护基石"
    },
    {
        "id": "DL-SEC-008",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "隐私保护与日志脱敏",
        "name": "日志与错误栈中 Cookie/Token 零暴露脱敏",
        "condition": "任何发生异常、打印日志或向前端返回错误堆栈的场景",
        "logic": "全局拦截器 _redact_public_url 与 safe_external_error_message 自动擦除 Cookie 键值与 xsec_token 查询参数",
        "code": "app/main.py:_redact_public_url:335",
        "result": "对外暴露的文字中绝对不含任何敏感密钥，完全脱敏展示",
        "safety": "符合个人信息安全与数据合规最严格要求"
    }
]

print(f"Loaded {len(DETAILED_LOGIC_BRANCHES)} extreme detailed logic branches.")
