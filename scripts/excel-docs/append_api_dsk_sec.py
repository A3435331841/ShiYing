# -*- coding: utf-8 -*-
"""Data definitions for API, Desktop, and Security features."""

API_DSK_SEC_FEATURES = [
    # -------------------------------------------------------------
    # 五、RESTful API与网络通信 (33项)
    # -------------------------------------------------------------
    {
        "id": "F-API-001",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "POST /api/jobs (创建下载任务)",
        "desc": "接收 url, auto_start, force, item_ids 等参数创建新下载任务；自动捕获当前最新配置；支持查重强制覆盖 (force=True)；返回创建成功的公共脱敏任务对象 (状态码 201)。",
        "trigger": "首页点击「解析并下载」或「仍然下载」",
        "code": "app/main.py: create_job(); CreateJobRequest",
        "status": "生产级就绪",
        "rules": "URL 长度限制 8-4096 字符；非法链接报 422；任务重复且非 force 抛异常。"
    },
    {
        "id": "F-API-002",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "GET /api/jobs (任务全量列表)",
        "desc": "返回当前系统中所有已创建任务列表，每个任务包含基本元数据、子作品条目明细、进度统计、输出文件列表，所有敏感参数与 Cookie 全程脱敏。",
        "trigger": "页面加载及轮询刷新时调用",
        "code": "app/main.py: list_jobs(); _public_job()",
        "status": "生产级就绪",
        "rules": "按创建时间顺序排列；移除敏感查询参数与内部临时 token。"
    },
    {
        "id": "F-API-003",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "GET /api/jobs/{id} (单个任务详情)",
        "desc": "按 job_id 查询特定任务的详细状态，不存在时返回 404；包含各个子媒体项的进度百分比、已读字节、瞬时速度和格式信息。",
        "trigger": "点击查看任务卡片详情弹窗时",
        "code": "app/main.py: get_job()",
        "status": "生产级就绪",
        "rules": "用于前端详情弹窗精确渲染；支持微信与通用任务。"
    },
    {
        "id": "F-API-004",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "POST /api/jobs/{id}/start (定向启动选集任务)",
        "desc": "接收 item_ids 数组启动指定的下载任务；若传入了选集过滤参数，只针对指定勾选的条目启动调度，其余未选条目保持跳过状态。",
        "trigger": "预览选集弹窗中勾选条目后确认下载时调用",
        "code": "app/main.py: start_job_with_selection(); StartJobRequest",
        "status": "生产级就绪",
        "rules": "item_ids 至少包含 1 项；未勾选启动返回 400；仅限处于未开始/初始态任务。"
    },
    {
        "id": "F-API-005",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "POST /api/jobs/{id}/pause (暂停下载任务)",
        "desc": "接收任务 ID，向底层工作线程下发暂停标记，将当前执行中的 item 状态切换为 PAUSED，安全保存已下载的临时分片并释放工作线程。",
        "trigger": "点击任务卡片上的「暂停」按钮",
        "code": "app/main.py: pause_job()",
        "status": "生产级就绪",
        "rules": "任务非运行态时调用保持幂等；返回最新的 public_job 对象。"
    },
    {
        "id": "F-API-006",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "POST /api/jobs/{id}/resume (恢复下载任务)",
        "desc": "将 PAUSED 状态的任务恢复排队调度，从上次暂停的断点位置继续拉取未完成分片，对历史因取消遗留的项自动纳入补做。",
        "trigger": "点击已暂停任务卡片上的「继续」按钮",
        "code": "app/main.py: resume_job()",
        "status": "生产级就绪",
        "rules": "仅作用于 PAUSED、INTERRUPTED 等暂停/中断任务。"
    },
    {
        "id": "F-API-007",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "POST /api/jobs/{id}/cancel (取消下载任务)",
        "desc": "安全终止指定任务的后台工作流程，清理未传输完成的损坏暂存文件，保留已经落盘的合格媒体文件，状态变迁为 CANCELLED。",
        "trigger": "任务卡片点击「取消」按钮",
        "code": "app/main.py: cancel_job()",
        "status": "生产级就绪",
        "rules": "取消过程带超时保护；已取消任务不再接受工作线程派发。"
    },
    {
        "id": "F-API-008",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "POST /api/jobs/{id}/retry (重试失败任务)",
        "desc": "接收可选的 item_id 参数。若提供 item_id 则仅重试该子项；未提供则重试当前任务下所有失败/需处理的子项；重新加载最新配置。",
        "trigger": "任务卡片或素材列表点击「重试」按钮",
        "code": "app/main.py: retry_job(); RetryRequest",
        "status": "生产级就绪",
        "rules": "对不可重试的项返回 409；重新生成调度队列。"
    },
    {
        "id": "F-API-009",
        "category": "RESTful API与网络通信",
        "subsystem": "核心任务端点",
        "name": "DELETE /api/jobs/{id} (删除任务记录)",
        "desc": "从系统状态数据库（data/state）中移除指定任务记录。若任务正在运行中则拒绝删除并返回 409；仅删除记录，不删本地物理媒体文件。",
        "trigger": "任务卡片或管理界面点击「移除记录」",
        "code": "app/main.py: delete_job_record()",
        "status": "生产级就绪",
        "rules": "运行态拦截保护；删除后返回 {deleted: true}。"
    },
    {
        "id": "F-API-010",
        "category": "RESTful API与网络通信",
        "subsystem": "查重与预览端点",
        "name": "GET /api/jobs/exists (查重任务检测)",
        "desc": "根据传入的 URL 进行平台与标准化作品来源指纹比对（忽略追踪参数），返回是否存在已有任务（exists: true/false）及已有任务的简明概要信息。",
        "trigger": "输入框粘贴链接提交前自动发起查重调用",
        "code": "app/main.py: find_existing_job(), _source_key()",
        "status": "生产级就绪",
        "rules": "对 YouTube 规范化到视频/Shorts ID；对抖音/小红书规范化到媒体ID。"
    },
    {
        "id": "F-API-011",
        "category": "RESTful API与网络通信",
        "subsystem": "查重与预览端点",
        "name": "POST /api/preview (作品解析预览)",
        "desc": "接收单作品 URL，探测提取平台名称、作者昵称、分P条目、分辨率及媒体类型；对作者主页作品过多场景，快速失败并返回 422 友好引导直接下载。",
        "trigger": "输入框填入链接后点击「预览选集」按钮",
        "code": "app/main.py: preview_link(); PreviewRequest",
        "status": "生产级就绪",
        "rules": "主页快速失败防挂起；普通视频最长等待 4 分钟；返回规范列表。"
    },
    {
        "id": "F-API-012",
        "category": "RESTful API与网络通信",
        "subsystem": "媒体与封面端点",
        "name": "GET /api/jobs/{id}/cover (获取任务封面缩略图)",
        "desc": "获取任务封面。优先从已落盘的本地媒体调用 FFmpeg 截取第 1 秒视频帧或返回本地图片；本地未就绪时代理拉取并缓存原始封面图，带 24 小时缓存头。",
        "trigger": "前端渲染任务列表卡片封面图标时由 img 标签请求",
        "code": "app/main.py: get_job_cover(), _extract_video_thumbnail()",
        "status": "生产级就绪",
        "rules": "缓存保存在 data/thumbnails 目录；图片均带正确 Content-Type。"
    },
    {
        "id": "F-API-013",
        "category": "RESTful API与网络通信",
        "subsystem": "桌面文件系统端点",
        "name": "POST /api/folder/open (打开下载目录/前台置顶)",
        "desc": "接收可选的 job_id。若带 job_id 打开该任务实际文件保存的作者/作品目录，不带则打开总下载目录；Windows 下通过 COM 唤醒已有资源管理器置顶。",
        "trigger": "点击「打开下载目录」或任务卡片上的「打开文件夹」",
        "code": "app/main.py: open_download_folder(); OpenFolderRequest",
        "status": "生产级就绪",
        "rules": "严格限制仅能打开任务合规的保存路径，拒绝越权跨目录访问。"
    },
    {
        "id": "F-API-014",
        "category": "RESTful API与网络通信",
        "subsystem": "桌面文件系统端点",
        "name": "POST /api/files/open (打开或高亮显示文件)",
        "desc": "接收 job_id, item_id, file_index, reveal。reveal=true 时在系统资源管理器中高亮选中文件；reveal=false 时调用系统默认播放器打开文件（严格白名单格式校验）。",
        "trigger": "点击文件名称直接播放，或点击「在文件夹中显示」",
        "code": "app/main.py: open_job_file(); OpenSavedFileRequest",
        "status": "生产级就绪",
        "rules": "非白名单媒体格式拒绝打开以防投毒；文件不存在时返回 404。"
    },
    {
        "id": "F-API-015",
        "category": "RESTful API与网络通信",
        "subsystem": "配置与环境端点",
        "name": "GET /api/config (读取当前应用配置)",
        "desc": "获取当前服务生效的全局配置对象，包括默认下载目录 download_dir、是否启用 Chrome Cookie (use_chrome_cookies) 及 Chrome 个人资料文件夹名 (chrome_profile)。",
        "trigger": "页面初始化及打开设置弹窗时请求",
        "code": "app/main.py: get_config(); AppConfig",
        "status": "生产级就绪",
        "rules": "带线程安全读锁；返回 AppConfig 深拷贝。"
    },
    {
        "id": "F-API-016",
        "category": "RESTful API与网络通信",
        "subsystem": "配置与环境端点",
        "name": "PUT /api/config (更新保存应用配置)",
        "desc": "更新全局配置。严格校验保存目录绝对路径合法性与可写性；对 chrome_profile 进行正则约束；采用原子临时文件替换方式写入 data/config.json 并实时生效。",
        "trigger": "设置弹窗中修改保存目录或 Cookie 选项点击「保存设置」",
        "code": "app/main.py: update_config(), _save_config()",
        "status": "生产级就绪",
        "rules": "目录不可写抛 422；写入失败回滚旧配置；实时刷新 manager 配置。"
    },
    {
        "id": "F-API-017",
        "category": "RESTful API与网络通信",
        "subsystem": "配置与环境端点",
        "name": "GET /api/environment (环境健康与依赖自检)",
        "desc": "对本机运行环境执行实时检测，检查 FFmpeg, FFprobe 可执行性、Chrome 安装路径、下载目录写入权限、yt-dlp 与 Playwright 包版本，返回结构化状态与中文提示。",
        "trigger": "打开「下载设置」面板时调用",
        "code": "app/main.py: environment_status(); app/environment.py: check_environment()",
        "status": "生产级就绪",
        "rules": "返回 checks 列表，指示各项 ok 状态与建议修复方案。"
    },
    {
        "id": "F-API-018",
        "category": "RESTful API与网络通信",
        "subsystem": "缓存与存储端点",
        "name": "GET /api/cache (查询分段下载缓存用量)",
        "desc": "统计 cache 临时目录下的有效文件数、所占磁盘总字节数，并检查当前活跃的 busy_jobs（QUEUED, DISCOVERING, DOWNLOADING），返回是否允许清理标记 (can_clear)。",
        "trigger": "侧边栏及设置面板展示缓存卡片时周期性调用",
        "code": "app/main.py: cache_status(), _cache_usage()",
        "status": "生产级就绪",
        "rules": "符号链接安全跳过；统计 busy 与 paused 状态任务数。"
    },
    {
        "id": "F-API-019",
        "category": "RESTful API与网络通信",
        "subsystem": "缓存与存储端点",
        "name": "POST /api/cache/clear (安全清理下载缓存)",
        "desc": "安全清理 cache 目录下的所有临时文件与分片。若当前存在正在下载的忙碌任务，主动拒绝并返回 409 冲突及忙碌任务数；清理成功返回释放的字节数。",
        "trigger": "侧边栏卡片或设置面板中点击「清理缓存」按钮",
        "code": "app/main.py: clear_cache()",
        "status": "生产级就绪",
        "rules": "仅删除 cache/ 目录内容；绝不触碰 downloads/ 已完成的文件。"
    },
    {
        "id": "F-API-020",
        "category": "RESTful API与网络通信",
        "subsystem": "认证与验证端点",
        "name": "POST /api/jobs/{id}/verify (唤起 Chrome 人机验证)",
        "desc": "针对处于 NEEDS_AUTH 状态的小红书等任务，核对任务作品身份及绑定 Profile 后，在用户系统 Chrome 中自动弹出目标作品页面，引导用户完成滑块或登录。",
        "trigger": "任务卡片显示需要验证时，点击「打开验证」",
        "code": "app/main.py: open_verification()",
        "status": "生产级就绪",
        "rules": "非 NEEDS_AUTH 任务调用抛 409；URL 必须通过严格白名单核验。"
    },
    {
        "id": "F-API-021",
        "category": "RESTful API与网络通信",
        "subsystem": "视频号专用端点",
        "name": "GET /api/wechat/status (视频号登录态状态查询)",
        "desc": "查询视频号元宝 Cookie 配置状态（cookie_configured: true/false），以及本地 Chrome 自动回退支持标记，不读取也不返回任何 Cookie 敏感数据。",
        "trigger": "进入设置面板打开视频号面板时查询",
        "code": "app/wechat.py: status()",
        "status": "生产级就绪",
        "rules": "只返回布尔标记，零信息泄露。"
    },
    {
        "id": "F-API-022",
        "category": "RESTful API与网络通信",
        "subsystem": "视频号专用端点",
        "name": "POST /api/wechat/cookie (保存/清除元宝 Cookie)",
        "desc": "接收单行 Cookie 请求头字符串；使用 Windows DPAPI 加密保存到 data/yuanbao-cookie.txt；若传入空字符串则安全删除本地已保存凭据并返回 {saved: true}。",
        "trigger": "在视频号设置中点击「保存登录态」或「清除登录态」",
        "code": "app/wechat.py: save_cookie(); CookieInput",
        "status": "生产级就绪",
        "rules": "带同源请求校验；禁止换行注入；加密存储。"
    },
    {
        "id": "F-API-023",
        "category": "RESTful API与网络通信",
        "subsystem": "视频号专用端点",
        "name": "POST /api/wechat/login (Playwright 扫码登录元宝)",
        "desc": "启动独立的 Chromium 浏览器窗口打开 yuanbao.tencent.com，等待用户扫码或登录，自动拦截并提取 hy_user 与 hy_token，加密写入本地密钥库。",
        "trigger": "视频号设置面板点击「扫码登录元宝」按钮",
        "code": "app/wechat.py: browser_login(), login_with_browser()",
        "status": "生产级就绪",
        "rules": "全局排他互斥锁防重复打开窗口；超时自动清理；凭据内存脱敏。"
    },
    {
        "id": "F-API-024",
        "category": "RESTful API与网络通信",
        "subsystem": "视频号专用端点",
        "name": "GET /api/wechat/jobs (旧视频号任务查询兼容)",
        "desc": "提供对历史旧版本视频号独立任务记录（data/wechat-jobs.json）的只读兼容查询，自动关联本地实际文件存在性与大小，支持老用户平滑迁移。",
        "trigger": "前端兼容读取历史视频号任务列表",
        "code": "app/wechat.py: list_jobs()",
        "status": "生产级就绪",
        "rules": "向后兼容支持；新任务已统一接入核心 DownloadManager。"
    },
    {
        "id": "F-API-025",
        "category": "RESTful API与网络通信",
        "subsystem": "视频号专用端点",
        "name": "DELETE /api/wechat/jobs/{id} (删除旧视频号记录)",
        "desc": "删除历史旧视频号任务记录，从 data/wechat-jobs.json 中安全移除对应条目，运行中的任务拦截并抛 409 冲突。",
        "trigger": "旧视频号记录列表点击删除",
        "code": "app/wechat.py: delete_job_record()",
        "status": "生产级就绪",
        "rules": "带同源校验；仅删记录保留文件。"
    },
    {
        "id": "F-API-026",
        "category": "RESTful API与网络通信",
        "subsystem": "视频号专用端点",
        "name": "POST /api/wechat/jobs/{id}/cancel (取消旧视频号任务)",
        "desc": "对历史旧视频号任务下发取消事件，平滑中断正在进行的元宝解析或二进制流下载，状态转为 cancelling / cancelled。",
        "trigger": "旧视频号任务卡片点击取消",
        "code": "app/wechat.py: cancel_job()",
        "status": "生产级就绪",
        "rules": "带同源校验；通知线程池安全收尾。"
    },
    {
        "id": "F-API-027",
        "category": "RESTful API与网络通信",
        "subsystem": "视频号专用端点",
        "name": "POST /api/wechat/jobs (旧创建入口停用拦截)",
        "desc": "旧版视频号独立创建任务接口已被正式废弃停用；直接返回 HTTP 410 状态码，并附带明确中文提示引导用户在首页主输入框统一创建新任务。",
        "trigger": "旧客户端调用旧创建接口时触发",
        "code": "app/wechat.py: create()",
        "status": "已停用 (返回 410)",
        "rules": "统一任务队列与生命周期；消除双系统割裂架构。"
    },
    {
        "id": "F-API-028",
        "category": "RESTful API与网络通信",
        "subsystem": "系统运行时与通信",
        "name": "GET /api/health (系统健康自检与构建指纹)",
        "desc": "输出应用名称 (APP_ID)、版本 (APP_VERSION)、当前构建指纹 (BUILD_ID)、源码指纹、是否需重启标记 (restart_required)、实例 ID、进程 PID、监听端口及配置文件灾难恢复通知。",
        "trigger": "桌面壳启动探活、前端页面建立连接时定期拉取",
        "code": "app/main.py: health()",
        "status": "生产级就绪",
        "rules": "对外透传只读系统健康指标；源码与内存不一致时指示重启。"
    },
    {
        "id": "F-API-029",
        "category": "RESTful API与网络通信",
        "subsystem": "系统运行时与通信",
        "name": "POST /api/runtime/stop (安全停机与令牌鉴权)",
        "desc": "通过 HTTP 接口平滑终止服务。严格限制仅限 127.0.0.1 本地调用，必须在请求头携带启动时生成的随机停止令牌，校验一致后触发 Uvicorn 安全退出。",
        "trigger": "运行 stop.py / stop.bat 时发起停机请求",
        "code": "app/main.py: stop_runtime(), _request_process_stop()",
        "status": "生产级就绪",
        "rules": "防止未经授权的关机攻击；等待响应报文发送完毕后安全停止。"
    },
    {
        "id": "F-API-030",
        "category": "RESTful API与网络通信",
        "subsystem": "系统运行时与通信",
        "name": "GET /api/events (Server-Sent Events 实时事件流)",
        "desc": "基于 text/event-stream 长连接向前端实时推送任务增量变更事件（event: job），内置 15 秒心跳保活（: keepalive），客户端断线自动重连。",
        "trigger": "Web 页面加载时自动建立 SSE 长连接",
        "code": "app/main.py: events(); app/event_stream.py: JobEventBuffer",
        "status": "生产级就绪",
        "rules": "连接释放时自动注销监听器；禁用代理缓冲（X-Accel-Buffering: no）。"
    },
    {
        "id": "F-API-031",
        "category": "RESTful API与网络通信",
        "subsystem": "安全网络中间件",
        "name": "TrustedHostMiddleware 本地主机限定",
        "desc": "基于 Starlette TrustedHostMiddleware 中间件，严格限定 HTTP Host 请求头仅能为 127.0.0.1 或 localhost，拦截任何来自局域网或外部域名的 DNS 劫持请求。",
        "trigger": "所有进入后端的 HTTP 请求在最外层经过该中间件",
        "code": "app/main.py: app.add_middleware(TrustedHostMiddleware)",
        "status": "生产级就绪",
        "rules": "禁止公网暴露访问；保证单机专属私密性。"
    },
    {
        "id": "F-API-032",
        "category": "RESTful API与网络通信",
        "subsystem": "安全网络中间件",
        "name": "local_mutations_only 防 CSRF 跨站攻击中间件",
        "desc": "自定义全局 HTTP 中间件，对所有非幂等写操作（POST, PUT, DELETE）核查 Origin 头部及 Sec-Fetch-Site 头部，拦截跨站恶意脚本与伪造表单提交。",
        "trigger": "所有写操作接口（如创建任务、改配置、删任务）触发",
        "code": "app/main.py: local_mutations_only()",
        "status": "生产级就绪",
        "rules": "检测到跨站请求直接返回 403 及「只接受本机拾影页面的操作」。"
    },
    {
        "id": "F-API-033",
        "category": "RESTful API与网络通信",
        "subsystem": "安全网络中间件",
        "name": "disable_runtime_asset_cache 防旧代码缓存中间件",
        "desc": "对根路径 / 及所有 /static/ 静态资源文件强制注入 Cache-Control: no-store, max-age=0 及 Pragma: no-cache 响应头，确保前端在服务升级后绝不加载陈旧 JS/CSS。",
        "trigger": "请求 HTML 页面及 static 静态资源时",
        "code": "app/main.py: disable_runtime_asset_cache()",
        "status": "生产级就绪",
        "rules": "避免用户浏览器强缓存旧前端代码导致的交互不一致或报错。"
    },

    # -------------------------------------------------------------
    # 六、桌面客户端集成与系统守护 (10项)
    # -------------------------------------------------------------
    {
        "id": "F-DSK-001",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "桌面入口与架构统一",
        "name": "统一端口与服务复用机制",
        "desc": "桌面客户端与独立 Web 浏览器入口统一规范在 127.0.0.1:8766 端口。启动桌面壳前先探测端口，若服务已在跑则直接复用已有后端，绝不重复拉起产生端口冲突。",
        "trigger": "双击「启动拾影」桌面快捷方式或运行启动脚本",
        "code": "launcher.py; launch_shiying.py; app/launcher_control.py",
        "status": "生产级就绪",
        "rules": "统一 8766，消除历史遗留的 9016 端口割裂问题。"
    },
    {
        "id": "F-DSK-002",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "桌面入口与架构统一",
        "name": "pywebview 原生桌面窗口集成",
        "desc": "基于 pywebview 框架与微软官方 Edge WebView2 运行时封装独立桌面窗口，具有原生阴影、自适应窗口初始尺寸、自定义品牌窗口图标与无缝体验。",
        "trigger": "运行 launch_shiying.py 或 启动拾影.vbs",
        "code": "launch_shiying.py; launcher.py",
        "status": "生产级就绪",
        "rules": "提供桌面原生应用质感；独立 WebView2 用户数据目录隔离在 data/webview。"
    },
    {
        "id": "F-DSK-003",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "桌面入口与架构统一",
        "name": "桌面壳平滑降级至系统浏览器",
        "desc": "启动时若检测到本机系统缺少 WebView2 运行时或图形环境初始化异常，系统不会崩溃，而是自动无缝降级并在默认系统浏览器中打开 http://127.0.0.1:8766。",
        "trigger": "在未安装 Edge WebView2 的 Windows 环境启动时",
        "code": "launcher.py: _start_browser()",
        "status": "生产级就绪",
        "rules": "无论桌面环境如何，均能 100% 保证用户可用。"
    },
    {
        "id": "F-DSK-004",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "操作系统深度交互",
        "name": "资源管理器窗口前台激活置顶 (open_folder_foreground)",
        "desc": "利用 Python.NET 与 Win32 API（Shell.Application 与 SetForegroundWindow），枚举已有资源管理器窗口，若目标文件夹已打开则直接前台激活置顶，避免重复开窗。",
        "trigger": "点击「打开文件夹」或「打开下载目录」时执行",
        "code": "app/desktop.py: open_folder_foreground()",
        "status": "生产级就绪",
        "rules": "在独立 STA 线程中运行 COM 调用；若前台置顶被系统策略限制则返回降级说明。"
    },
    {
        "id": "F-DSK-005",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "操作系统深度交互",
        "name": "原生目录选择对话框 (Native FolderPicker)",
        "desc": "在桌面模式下修改保存目录时，通过 pywebview 的 create_file_dialog 提供系统原生文件夹选择对话框，用户图形化点击选取，无需手动繁琐复制粘贴绝对路径。",
        "trigger": "在桌面窗口中点击设置界面的「选择文件夹」按钮",
        "code": "app/static/home.js; launcher.py",
        "status": "生产级就绪",
        "rules": "路径自动回填并提交服务端校验可写性。"
    },
    {
        "id": "F-DSK-006",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "进程守护与安全启停",
        "name": "内核级单实例互斥锁",
        "desc": "在启动、依赖安装与后台运行时共用内核单实例互斥锁，防止用户多次手滑连续双击启动脚本导致多个后端并发争抢端口、竞争写入任务数据库。",
        "trigger": "启动程序瞬间由启动器获取系统互斥锁",
        "code": "launcher.py: SingleInstanceLock",
        "status": "生产级就绪",
        "rules": "检测到已有实例运行自动切换为连接已有服务或前台激活。"
    },
    {
        "id": "F-DSK-007",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "进程守护与安全启停",
        "name": "Windows Job Object 进程树级联清理守护",
        "desc": "基于 Win32 Job Object（SetInformationJobObject 配合 JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE），将 Python 服务、FFmpeg 探测/转码进程及 Playwright 浏览器进程绑定为原子进程树，父进程意外终止时内核自动回收所有子进程，绝不留僵尸后台。",
        "trigger": "Windows 下启动服务时自动初始化创建",
        "code": "app/process_guard.py: enable_process_tree_cleanup()",
        "status": "生产级就绪",
        "rules": "即使用户强行在任务管理器杀父控制台，子进程也会瞬间一并回收。"
    },
    {
        "id": "F-DSK-008",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "进程守护与安全启停",
        "name": "精准受控停机机制 (stop.py / stop.bat)",
        "desc": "停止脚本先核对随机实例 ID、PID、构建指纹和保存在本机的停止密钥，全部一致才通过 HTTP 发出安全停机指令，安全释放任务与端口；绝不按进程名暴力杀灭系统所有 Python。",
        "trigger": "双击运行「停止拾影.bat」或运行 stop.py / stop.sh",
        "code": "stop.py; app/runtime.py; app/main.py: /api/runtime/stop",
        "status": "生产级就绪",
        "rules": "确保正在写入的文件安全落盘；释放互斥锁并清理临时状态。"
    },
    {
        "id": "F-DSK-009",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "开箱即用运行套件",
        "name": "多平台一键运行脚本全家桶",
        "desc": "配套提供 启动拾影.vbs（无黑框静默运行）、启动拾影.bat、停止拾影.bat、start.bat、stop.bat、start.sh、stop.sh、start.command、stop.command，全面覆盖 Windows、macOS 与 Linux。",
        "trigger": "用户双击对应的脚本文件",
        "code": "项目根目录下的各类 .bat, .vbs, .sh, .command 脚本",
        "status": "生产级就绪",
        "rules": "自动激活项目内 .venv 虚拟环境并引导依赖环境。"
    },
    {
        "id": "F-DSK-010",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "开箱即用运行套件",
        "name": "自动化依赖安装与环境管理",
        "desc": "提供 安装依赖.bat，基于高性能 uv 包管理器极速安装与对齐 requirements.lock.txt，自动补全项目运行所需的 Playwright 依赖及 Deno 运行时组件。",
        "trigger": "首次解压部署或更新代码后双击「安装依赖.bat」",
        "code": "安装依赖.bat; pyproject.toml; requirements.txt",
        "status": "生产级就绪",
        "rules": "统一锁定 Python 3.10+ 环境版本与构建依赖指纹。"
    },

    # -------------------------------------------------------------
    # 七、安全防护、反风控与故障诊断 (8项)
    # -------------------------------------------------------------
    {
        "id": "F-SEC-001",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "安全沙箱与防投毒",
        "name": "本地媒体文件打开扩展名严格白名单",
        "desc": "严禁从界面执行可执行文件与脚本。系统定义 OPENABLE_MEDIA_SUFFIXES 白名单，严格限制仅限 mp4, mkv, webm, jpg, png, heic 等 20 余种多媒体后缀，全面防范恶意构造下载物导致的本地代码执行漏洞。",
        "trigger": "通过 API 打开下载文件时后端自动拦截核验",
        "code": "app/desktop.py: OPENABLE_MEDIA_SUFFIXES, can_open_saved_media()",
        "status": "生产级就绪",
        "rules": "非白名单文件拒绝调用系统默认程序打开；严格路径穿越防范。"
    },
    {
        "id": "F-SEC-002",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "安全沙箱与防投毒",
        "name": "媒体直链与重定向 URL 严格安全白名单与逐跳校验",
        "desc": "在发起网络请求前，强制核验 URL 协议（仅限 HTTPS）、标准端口（仅限 443）、域名官方白名单，逐跳校验重定向目标，拦截 0.0.0.0、127.0.0.1、内网 IP 与类似域名（如 *.evil.test），彻底阻断 SSRF 攻击。",
        "trigger": "各平台下载视频、图片与重定向跳转时自动执行",
        "code": "app/engine/transfer.py; app/bilibili_opus.py; app/wechat.py: _trusted_media_url()",
        "status": "生产级就绪",
        "rules": "各平台维护严格域名白名单（如 bilivideo.com, kwaicdn.com, bytecdn.cn 等）；非受信一律拒绝。"
    },
    {
        "id": "F-SEC-003",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "安全沙箱与防投毒",
        "name": "用户凭证与敏感数据全链路脱敏隔离",
        "desc": "视频号元宝 Cookie 采用 DPAPI 密文存储；Chrome Cookie 仅限必要时只读内存提取，不打日志、不存文件、不向前端接口回传；对外接口自动通过 _redact_public_url 脱敏 xsec_token 等敏感参数。",
        "trigger": "任务序列化、日志打印及前端 API 返回数据时",
        "code": "app/main.py: _redact_public_url(), _redact_public_value()",
        "status": "生产级就绪",
        "rules": "杜绝用户敏感账号凭据在日志、截屏或网络抓包中泄漏。"
    },
    {
        "id": "F-SEC-004",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "配置灾难恢复",
        "name": "配置文件自动容错备份与自愈恢复",
        "desc": "若 data/config.json 发生断电损坏或被非法篡改导致 JSON 反序列化失败，程序不崩溃，而是自动将损坏文件安全重命名备份为 config.corrupt-YYYYMMDD-HHMMSS.json，自动启用默认配置并在界面明示提醒重新保存。",
        "trigger": "服务启动读取配置文件遇到损坏异常时",
        "code": "app/main.py: _load_config(); CONFIG_RECOVERY",
        "status": "生产级就绪",
        "rules": "绝不静默覆盖用户损坏的旧配置，便于后续排查恢复。"
    },
    {
        "id": "F-SEC-005",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "异常分类与智能诊断",
        "name": "15 大标准化站点故障分类体系 (SiteIssueCode)",
        "desc": "将复杂的底层网络与平台报错归纳为 15 种标准类别：频控限制 (RATE_LIMITED)、需验证码 (VERIFICATION_REQUIRED)、需登录 (LOGIN_REQUIRED)、请求被拒 (REQUEST_REJECTED)、站点处理中 (SITE_PROCESSING)、内容失效 (CONTENT_UNAVAILABLE)、地区限制 (REGION_RESTRICTED)、接口变动 (SITE_RESPONSE_CHANGED)、直链过期 (MEDIA_LINK_EXPIRED)、站点故障 (SITE_UNAVAILABLE)、网络超时 (NETWORK_ERROR)、Cookie失效 (COOKIE_UNAVAILABLE)、安全拦截 (SECURITY_BLOCKED)、本地配置 (LOCAL_CONFIGURATION)、未知错误 (UNKNOWN)。",
        "trigger": "任务或子项发生任何解析或下载异常时触发分类",
        "code": "app/errors.py: SiteIssueCode, classify_site_issue()",
        "status": "生产级就绪",
        "rules": "稳定状态码体系；支持精准定位问题性质。"
    },
    {
        "id": "F-SEC-006",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "异常分类与智能诊断",
        "name": "固定脱敏诊断码传播机制",
        "desc": "从平台底层异常捕获到任务状态持久化再到前端 API 展现，全程传递脱敏后的固定分类诊断码（如 detail-metadata-incomplete、signed-rejected），绝不直接拼接裸露的底层异常调用栈或可能携带凭据的原始 HTTP 响应。",
        "trigger": "异常抛出并包装为 DownloadItem.diagnostic_code 时",
        "code": "app/manager/issues.py: _record_issue_locked()",
        "status": "生产级就绪",
        "rules": "防止敏感响应内容回显；便于技术支持标准化排障。"
    },
    {
        "id": "F-SEC-007",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "异常分类与智能诊断",
        "name": "“发生了什么 + 如何解决”双轨用户指引",
        "desc": "前端错误提示模块将晦涩的技术报错拆解为清晰的白话指引：明确区分「发生了什么」（如：平台提示需要登录或拖动滑块验证）和可执行的「解决办法」（如：请打开 Chrome 登录后退出，或使用内置扫码），只有明确的凭据证据才提示相关操作。",
        "trigger": "任务卡片展开错误详情或界面提示条展示时",
        "code": "app/static/home.js; app/errors.py",
        "status": "生产级就绪",
        "rules": "极佳的用户体验设计；普通非技术用户也能快速自行解决常见风控。"
    },
    {
        "id": "F-SEC-008",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "安全辅助工具",
        "name": "专属 Chrome 扩展免抓包登录态提取",
        "desc": "提供 extension/yuanbao-login-copy 扩展程序（内含 manifest.json, popup.html, popup.js），用户在已登录元宝的 Chrome 中一键读取并复制未过期 Cookie 到剪贴板，纯本地运行、不联网、不收集任何隐私数据。",
        "trigger": "用户在 Chrome 扩展中点击复制元宝登录态",
        "code": "extension/yuanbao-login-copy/popup.js",
        "status": "生产级就绪",
        "rules": "权限严格限定仅限 yuanbao.tencent.com；提示用户粘贴后清空剪贴板。"
    },
]

# Write to features_data.py
with open("features_data.py", "a", encoding="utf-8") as f:
    f.write("\nALL_FEATURES.extend(API_DSK_SEC_FEATURES)\n")
    f.write("print(f'Total features in full matrix: {len(ALL_FEATURES)}')\n")

print("API, DSK, and SEC features appended.")
