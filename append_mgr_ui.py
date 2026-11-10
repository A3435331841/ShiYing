# -*- coding: utf-8 -*-
"""Append Manager and UI features to features_data.py."""

MGR_AND_UI_FEATURES = [
    # -------------------------------------------------------------
    # 三、任务调度与生命周期管理 (12项)
    # -------------------------------------------------------------
    {
        "id": "F-MGR-001",
        "category": "任务调度与生命周期管理",
        "subsystem": "任务队列与并发控制",
        "name": "统一多任务队列调度器",
        "desc": "基于 DownloadManager 统一纳管所有平台的下载任务，支持线程池排队执行、并发限制控制、平滑任务派发及服务关闭时的排空处理。",
        "trigger": "提交新任务或启动已有排队任务时触发",
        "code": "app/manager/core.py: DownloadManager",
        "status": "生产级就绪",
        "rules": "跨平台统一队列；避免过高并发被平台封禁 IP。"
    },
    {
        "id": "F-MGR-002",
        "category": "任务调度与生命周期管理",
        "subsystem": "任务生命周期状态机",
        "name": "10 种标准化任务生命周期状态机",
        "desc": "严格维护 QUEUED, DISCOVERING, DOWNLOADING, NEEDS_AUTH, COMPLETED, PARTIAL, FAILED, CANCELLED, INTERRUPTED, PAUSED 等 10 种状态流转，状态转换带有原子互斥锁保护。",
        "trigger": "任务在整个处理生命周期中自动跃迁",
        "code": "app/models.py: JobStatus, ItemStatus; app/manager/lifecycle.py",
        "status": "生产级就绪",
        "rules": "状态变迁实时通过 SSE 推送前端；杜绝无效非法状态越迁。"
    },
    {
        "id": "F-MGR-003",
        "category": "任务调度与生命周期管理",
        "subsystem": "暂停与恢复机制",
        "name": "运行中任务实时一键暂停 (pause_job)",
        "desc": "支持在下载过程中暂停任务，当前正在下载的文件安全标记为 PAUSED，保留已有分片缓存，立即释放后台工作线程，不浪费已有流量。",
        "trigger": "任务卡片点击「暂停」按钮或调用 /api/jobs/{id}/pause",
        "code": "app/manager/lifecycle.py: pause_job()",
        "status": "生产级就绪",
        "rules": "暂停状态写入持久化磁盘；不丢弃已下载的字节数据。"
    },
    {
        "id": "F-MGR-004",
        "category": "任务调度与生命周期管理",
        "subsystem": "暂停与恢复机制",
        "name": "暂停任务断点恢复继续 (resume_job)",
        "desc": "点击「继续」时将任务状态置回排队并从中断点恢复调度，不仅重新处理 PAUSED 的当前文件，还能自动补做旧版本因取消而遗留的未完成文件。",
        "trigger": "任务卡片点击「继续」按钮或调用 /api/jobs/{id}/resume",
        "code": "app/manager/lifecycle.py: resume_job()",
        "status": "生产级就绪",
        "rules": "复用未完成分片缓存继续拉取，按用户最初选集范围完整履约。"
    },
    {
        "id": "F-MGR-005",
        "category": "任务调度与生命周期管理",
        "subsystem": "细粒度重试机制",
        "name": "子项级单条精准重试 (retry_item)",
        "desc": "对于包含多子项的主页或图集任务，支持在任务详情面板针对特定失败或被取消的单个作品发起重试，不影响其余已完成项。",
        "trigger": "任务详情弹窗中点击具体某项右侧的「重试」",
        "code": "app/manager/core.py: retry_item()",
        "status": "生产级就绪",
        "rules": "单条重试不破坏其他排队项的状态；精确增量补齐。"
    },
    {
        "id": "F-MGR-006",
        "category": "任务调度与生命周期管理",
        "subsystem": "细粒度重试机制",
        "name": "任务级一键重试全部失败项 (retry_failed)",
        "desc": "针对状态为 PARTIAL 或 FAILED 的任务，支持一键重试其下所有处于 FAILED、CANCELLED 或 NEEDS_AUTH 状态的子项，已成功文件自动跳过并保留。",
        "trigger": "任务卡片点击「重试」按钮或调用 /api/jobs/{id}/retry",
        "code": "app/manager/core.py: retry_failed()",
        "status": "生产级就绪",
        "rules": "重新加载当前最新配置（如已更新的 Cookie）后执行重试。"
    },
    {
        "id": "F-MGR-007",
        "category": "任务调度与生命周期管理",
        "subsystem": "任务取消与资源释放",
        "name": "运行中任务安全取消与状态固化",
        "desc": "支持用户随时取消正在发现或传输中的任务，底层立即下发 CancelCallback 信号，平滑中断网络连接并清理未闭合临时文件，已下载的合法成品文件完整保留。",
        "trigger": "任务卡片点击「取消」按钮或调用 /api/jobs/{id}/cancel",
        "code": "app/manager/lifecycle.py: cancel_job()",
        "status": "生产级就绪",
        "rules": "状态明确终结为 CANCELLED，不残留僵尸工作线程。"
    },
    {
        "id": "F-MGR-008",
        "category": "任务调度与生命周期管理",
        "subsystem": "任务记录与数据清理",
        "name": "单条任务记录安全移除 (delete_job_record)",
        "desc": "支持移除指定非运行中任务的记录，从本地状态数据库（data/state）中安全擦除元数据，界面同步消失。",
        "trigger": "任务卡片点击「移除记录」或调用 DELETE /api/jobs/{id}",
        "code": "app/manager/core.py: delete_job_record()",
        "status": "生产级就绪",
        "rules": "仅删除元数据与任务记录，绝不静默物理删除本地已下载的多媒体文件，防止误删。"
    },
    {
        "id": "F-MGR-009",
        "category": "任务调度与生命周期管理",
        "subsystem": "任务记录与数据清理",
        "name": "批量管理与多选移除已完成记录",
        "desc": "在「已完成」页面提供「批量管理」模式，支持全选当前筛选结果或按复选框多选，一键批量清除所选已完成任务的历史记录。",
        "trigger": "「已完成」视图点击「批量管理」-> 勾选 -> 点击「移除所选记录」",
        "code": "app/static/home.js; app/main.py: delete_job_record()",
        "status": "生产级就绪",
        "rules": "弹窗提示受影响条目数，明确告知不删物理文件，用户确认后生效。"
    },
    {
        "id": "F-MGR-010",
        "category": "任务调度与生命周期管理",
        "subsystem": "存储容灾与状态自愈",
        "name": "本地任务状态原子持久化存储 (JsonJobStore)",
        "desc": "任务状态以 JSON 格式持久化在 data/state 目录下，采用临时文件写入 + fsync + 原子 replace 的方式落盘，防止断电或强制杀进程导致记录损坏。",
        "trigger": "任务创建、进度更新、状态跃迁时自动保存",
        "code": "app/storage.py: JsonJobStore",
        "status": "生产级就绪",
        "rules": "带线程安全锁保护；写入前创建必要目录。"
    },
    {
        "id": "F-MGR-011",
        "category": "任务调度与生命周期管理",
        "subsystem": "存储容灾与状态自愈",
        "name": "损坏任务文件自动隔离与告警",
        "desc": "系统启动或加载任务时，若发现某条任务 JSON 损坏无法反序列化，自动跳过该文件并在健康检查接口记录 unreadable_job_files 警告，绝不让单个损坏记录导致软件无法启动。",
        "trigger": "服务启动初始化加载任务列表时",
        "code": "app/storage.py; app/main.py: health()",
        "status": "生产级就绪",
        "rules": "在界面顶部通过 Recovery 条幅向用户展示受影响文件数量。"
    },
    {
        "id": "F-MGR-012",
        "category": "任务调度与生命周期管理",
        "subsystem": "存储容灾与状态自愈",
        "name": "异常关机后任务中断状态自愈",
        "desc": "当服务重启加载历史时，若发现有任务处于 DOWNLOADING, DISCOVERING 或 QUEUED 等未完成状态，自动将其跃迁标记为 INTERRUPTED，提示「上次运行已中断，请重新下载」。",
        "trigger": "服务崩溃或电脑重启后重新打开拾影时",
        "code": "app/manager/core.py: _reconcile_loaded_jobs()",
        "status": "生产级就绪",
        "rules": "消除假运行状态；用户点击重试即可原地断点恢复。"
    },

    # -------------------------------------------------------------
    # 四、前端交互与现代UI系统 (31项)
    # -------------------------------------------------------------
    {
        "id": "F-UI-001",
        "category": "前端交互与现代UI系统",
        "subsystem": "智能输入与文案提取",
        "name": "智能多链接与复杂分享文案正则提取",
        "desc": "输入框支持直接粘贴带有表情、标题、作者和邀请码的整段手机分享文案，也支持粘贴 Markdown 超链接，自动清洗并提取出标准的 HTTP/HTTPS 链接，单次最多支持 50 条批量提取。",
        "trigger": "将任意平台复制的整段文案粘贴到主输入框",
        "code": "app/static/home.js: extractShareLinks(); app/platforms.py: extract_url()",
        "status": "生产级就绪",
        "rules": "去除首尾标点（如括号、句号、书名号）；识别换行或空格分隔的多链接。"
    },
    {
        "id": "F-UI-002",
        "category": "前端交互与现代UI系统",
        "subsystem": "智能输入与文案提取",
        "name": "键盘极速提交快捷键 (Ctrl + Enter)",
        "desc": "输入框支持 Ctrl + Enter（Mac 上为 Cmd + Enter）快捷键直接触发「解析并下载」，提升高频采集效率。",
        "trigger": "在输入框内聚焦时按下键盘 Ctrl + Enter",
        "code": "app/static/home.js: keydown listener",
        "status": "生产级就绪",
        "rules": "输入框为空或仅有空白时禁用快捷键。"
    },
    {
        "id": "F-UI-003",
        "category": "前端交互与现代UI系统",
        "subsystem": "智能输入与文案提取",
        "name": "系统剪贴板快速粘贴按钮",
        "desc": "在分享链接标签旁提供「粘贴」按钮，一键调用浏览器 Clipboard API 读取剪贴板文本并填入输入框，无需手动右键。",
        "trigger": "点击输入框上方的「粘贴」文字按钮",
        "code": "app/static/home.js: navigator.clipboard.readText()",
        "status": "生产级就绪",
        "rules": "检测浏览器剪贴板权限，遇拒绝时提示用户手动按 Ctrl+V。"
    },
    {
        "id": "F-UI-004",
        "category": "前端交互与现代UI系统",
        "subsystem": "下载前预览与选集",
        "name": "作品解析预览与分P选集弹窗",
        "desc": "点击「预览选集」时异步请求 /api/preview，弹出作品解析预览弹窗，展示平台标识、作品标题、作者、分P/选集总数、每集清晰度与分P序号。",
        "trigger": "输入有效链接后点击「预览选集」按钮",
        "code": "app/static/home.js: #preview-dialog; app/main.py: /api/preview",
        "status": "生产级就绪",
        "rules": "请求最长等待 4 分钟；期间显示已耗时秒数，不误报超时。"
    },
    {
        "id": "F-UI-005",
        "category": "前端交互与现代UI系统",
        "subsystem": "下载前预览与选集",
        "name": "分集按需勾选与定向启动下载",
        "desc": "选集弹窗内提供「全选」、「全不选」与单项复选框，动态计算已选数量。点击「下载选中项 (N)」仅针对勾选条目定向创建并启动任务，未选条目完全不下载。",
        "trigger": "在预览选集弹窗中勾选条目后点击底部「下载选中项」",
        "code": "app/static/home.js: #confirm-download-selected; app/main.py: /api/jobs/{id}/start",
        "status": "生产级就绪",
        "rules": "若用户一项都不选，主按钮自动禁用并提示至少选择一项。"
    },
    {
        "id": "F-UI-006",
        "category": "前端交互与现代UI系统",
        "subsystem": "重复任务检测与确认",
        "name": "重复任务智能拦截与确认弹窗",
        "desc": "创建任务前调用 /api/jobs/exists，基于规范化后的平台与作品唯一身份匹配已有记录。若已存在，弹出确认框，展示已有任务标题、状态及创建时间，杜绝重复下载。",
        "trigger": "粘贴已下载过或已在列表中的相同链接时触发",
        "code": "app/static/home.js: #duplicate-dialog; app/main.py: /api/jobs/exists",
        "status": "生产级就绪",
        "rules": "提供「跳过这些链接」与「仍然下载」两种抉择；仍然下载时重名自动加 (2)。"
    },
    {
        "id": "F-UI-007",
        "category": "前端交互与现代UI系统",
        "subsystem": "工作台布局与导航",
        "name": "首页三段式清晰视觉层级",
        "desc": "工作台采用现代设计语言重构，分为「添加下载（Composer）-> 平台快捷入口 -> 最近任务列表」三段式流式布局，信息层级分明，视觉清晰舒适。",
        "trigger": "打开拾影主页默认呈现",
        "code": "app/static/index.html: main.workspace; app/static/home.css",
        "status": "生产级就绪",
        "rules": "符合现代设计规范；主操作区域首屏清晰可见。"
    },
    {
        "id": "F-UI-008",
        "category": "前端交互与现代UI系统",
        "subsystem": "工作台布局与导航",
        "name": "7 大平台快捷直达芯片与品牌图标",
        "desc": "输入框下方提供抖音、抖音精选、快手、视频号、小红书、B站、YouTube 7 大平台的彩色品牌徽章芯片，点击直接在默认浏览器中打开各平台官方站点。",
        "trigger": "点击输入框下方的各平台彩色芯片标签",
        "code": "app/static/index.html: .platform-row; app/static/platform-icons/*",
        "status": "生产级就绪",
        "rules": "配有 SVG 及高精 PNG 图标；附带明确中文悬停帮助文字。"
    },
    {
        "id": "F-UI-009",
        "category": "前端交互与现代UI系统",
        "subsystem": "多维过滤与检索系统",
        "name": "未完成任务状态 Tab 筛选",
        "desc": "在「下载任务」视图下提供：全部未完成、进行中、已暂停、需处理 4 组状态切换 Tab，每个 Tab 实时呈现动态数量徽章。",
        "trigger": "点击侧边栏「下载任务」并在顶部切换 Tab",
        "code": "app/static/home.js: #task-tabs button",
        "status": "生产级就绪",
        "rules": "快速聚合当前处于异常、断网或需验证的待处理事项。"
    },
    {
        "id": "F-UI-010",
        "category": "前端交互与现代UI系统",
        "subsystem": "多维过滤与检索系统",
        "name": "已完成内容类型 Tab 筛选",
        "desc": "在「已完成」视图下提供按作品媒体形态筛选的 Tab：全部类型、视频、音频、图文、其他，满足用户快速查找归档多媒体作品的诉求。",
        "trigger": "点击侧边栏「已完成」并在顶部切换 Tab",
        "code": "app/static/home.js: #media-tabs button",
        "status": "生产级就绪",
        "rules": "依据任务 items 下的实际 media_type 精准过滤。"
    },
    {
        "id": "F-UI-011",
        "category": "前端交互与现代UI系统",
        "subsystem": "多维过滤与检索系统",
        "name": "已完成高级筛选与排序下拉控件",
        "desc": "提供平台下拉筛选框（支持全部、抖音、快手、视频号、小红书、B站、YouTube等），以及排序下拉框（最近完成、最早完成、按标题排序），高效查找历史内容。",
        "trigger": "「已完成」视图中使用顶部下拉选择框",
        "code": "app/static/home.js: #completed-platform, #completed-sort",
        "status": "生产级就绪",
        "rules": "筛选条件组合生效，与搜索框联动。"
    },
    {
        "id": "F-UI-012",
        "category": "前端交互与现代UI系统",
        "subsystem": "多维过滤与检索系统",
        "name": "实时搜索过滤输入框",
        "desc": "列表上方提供实时搜索框，用户输入关键词时毫秒级即时过滤当前列表中的任务标题与作者昵称，无卡顿与重绘闪烁。",
        "trigger": "在任务列表右上角的搜索框输入文本",
        "code": "app/static/home.js: #task-search input listener",
        "status": "生产级就绪",
        "rules": "支持中英文模糊匹配；清空搜索框时即时还原全量列表。"
    },
    {
        "id": "F-UI-013",
        "category": "前端交互与现代UI系统",
        "subsystem": "任务卡片监控与展示",
        "name": "任务卡片平台主题色条与状态胶囊",
        "desc": "每个任务卡片左侧带有对应平台的品牌识别竖彩条，卡片标题旁呈现带有动态状态小圆点的半圆状态胶囊（如 进行中、已暂停、已完成、需处理、已中断）。",
        "trigger": "任务呈现在主界面列表时自动渲染",
        "code": "app/static/home.js: renderTaskItem(); app/static/home.css",
        "status": "生产级就绪",
        "rules": "高辨识度色彩语义；失败原因单行截断并可点击查看详情。"
    },
    {
        "id": "F-UI-014",
        "category": "前端交互与现代UI系统",
        "subsystem": "任务卡片监控与展示",
        "name": "真实下载进度与速率监控",
        "desc": "实时展示精确已下载字节数（如 14.2 MB / 45.8 MB）、动态百分比进度条、瞬时下载速率（如 5.4 MB/s）与预计剩余时间（ETA），无总大小时绝不显示虚假百分比。",
        "trigger": "任务处于 DOWNLOADING 状态时实时刷新",
        "code": "app/static/home.js; app/models.py: TransferProgress",
        "status": "生产级就绪",
        "rules": "基于 SSE 实时事件驱动；平滑计算瞬时网速。"
    },
    {
        "id": "F-UI-015",
        "category": "前端交互与现代UI系统",
        "subsystem": "任务卡片监控与展示",
        "name": "多媒体素材明细折叠展开",
        "desc": "针对包含多张图片或实况相片的任务，支持点击展开「查看全部素材」，逐条列出每个文件的序号、实际扩展名、真实分辨率（如 1080x1920）及文件体积。",
        "trigger": "点击多图任务卡片上的「展开素材列表」",
        "code": "app/static/home.js: #details-dialog, renderTaskDetails()",
        "status": "生产级就绪",
        "rules": "每个素材独立展示是否存在、是否可打开；支持单项重试。"
    },
    {
        "id": "F-UI-016",
        "category": "前端交互与现代UI系统",
        "subsystem": "任务卡片监控与展示",
        "name": "任务封面与本地截帧缩略图展示",
        "desc": "任务卡片左侧展示高清封面图。优先通过本地已下载的真实视频调用 FFmpeg 在 1 秒处截帧并缓存在本地，未就绪时加载元数据中的原始封面并本地化缓存。",
        "trigger": "渲染任务卡片缩略图或调用 /api/jobs/{id}/cover 时",
        "code": "app/main.py: get_job_cover(), _extract_video_thumbnail()",
        "status": "生产级就绪",
        "rules": "本地缩略图带有 24 小时 HTTP 缓存头；不存在时返回优雅占位图标。"
    },
    {
        "id": "F-UI-017",
        "category": "前端交互与现代UI系统",
        "subsystem": "本地文件交互与定位",
        "name": "本地媒体文件直接播放/查看",
        "desc": "已下载完成的媒体项支持点击文件名或「打开」，后端进行严格的安全扩展名校验后，直接调用 Windows 系统关联的默认播放器（如 Windows Media Player、PotPlayer）或图片查看器打开。",
        "trigger": "在任务卡片或素材列表中点击文件名/打开",
        "code": "app/main.py: /api/files/open; app/desktop.py: open_saved_file()",
        "status": "生产级就绪",
        "rules": "白名单限制仅限多媒体后缀；禁止打开可执行程序与脚本。"
    },
    {
        "id": "F-UI-018",
        "category": "前端交互与现代UI系统",
        "subsystem": "本地文件交互与定位",
        "name": "资源管理器定位高亮选中 (Reveal in Explorer)",
        "desc": "在详情列表点击「在文件夹中显示」，调用 Windows Shell 命令（explorer.exe /select, <path>），直接在资源管理器中定位并以高亮状态选中该文件，无需用户自己找文件。",
        "trigger": "素材条目点击「在文件夹中显示」",
        "code": "app/main.py: /api/files/open?reveal=true; app/desktop.py: open_saved_file()",
        "status": "生产级就绪",
        "rules": "跨平台适配（macOS open -R，Linux xdg-open）；检验路径真实存在性。"
    },
    {
        "id": "F-UI-019",
        "category": "前端交互与现代UI系统",
        "subsystem": "本地文件交互与定位",
        "name": "一键打开作品所属文件夹与前台置顶",
        "desc": "任务卡片点击「打开文件夹」，直接打开该任务实际文件落盘所在的作者或作品文件夹；通过 Win32 API 自动搜索已有 Explorer 窗口并置顶激活到屏幕前台，避免产生大量重复文件夹窗口。",
        "trigger": "任务卡片右侧操作栏点击「打开文件夹」",
        "code": "app/main.py: /api/folder/open; app/desktop.py: open_folder_foreground()",
        "status": "生产级就绪",
        "rules": "若系统阻止前台置顶，即时在界面给出清晰反馈与路径提示。"
    },
    {
        "id": "F-UI-020",
        "category": "前端交互与现代UI系统",
        "subsystem": "本地文件交互与定位",
        "name": "侧边栏一键打开下载总目录",
        "desc": "侧边栏底部提供「打开下载目录」全局按钮，点击直接唤起系统资源管理器打开 downloads 根目录，便于用户统览所有平台与作者内容。",
        "trigger": "点击侧边栏底部的「打开下载目录」按钮",
        "code": "app/main.py: /api/folder/open (无 job_id 参数)",
        "status": "生产级就绪",
        "rules": "使用配置中当前生效的 download_dir 路径；保证目录已自动创建。"
    },
    {
        "id": "F-UI-021",
        "category": "前端交互与现代UI系统",
        "subsystem": "存储与缓存管理UI",
        "name": "侧边栏下载缓存用量监控与清理入口",
        "desc": "侧边栏常驻显示「下载缓存」卡片，实时呈现分段下载缓存的大小（如 128 MB / 0 B），附带一键「清理」按钮与安全提示说明（清缓存不影响已下载文件）。",
        "trigger": "侧边栏直接观察或点击「清理」按钮",
        "code": "app/static/index.html: .sidebar-card; app/static/home.js: #sidebar-clear-cache",
        "status": "生产级就绪",
        "rules": "有正在下载的任务时清理按钮自动禁用，防误删传输中断。"
    },
    {
        "id": "F-UI-022",
        "category": "前端交互与现代UI系统",
        "subsystem": "设置抽屉与配置管理",
        "name": "设置抽屉：保存目录修改与原生对话框",
        "desc": "在「下载设置」弹窗中支持查看与修改保存目录，支持手动输入任意合规绝对路径，桌面窗口环境下可点击「选择文件夹」呼出系统原生 FolderPicker 目录选择框。",
        "trigger": "点击侧边栏或输入框下方的「设置 / 更改」按钮",
        "code": "app/static/home.js: #settings-dialog; app/main.py: PUT /api/config",
        "status": "生产级就绪",
        "rules": "提交时服务端验证目录可写性；新设置只影响新任务，旧任务保留原路径。"
    },
    {
        "id": "F-UI-023",
        "category": "前端交互与现代UI系统",
        "subsystem": "设置抽屉与配置管理",
        "name": "设置抽屉：运行环境依赖实时自检指示",
        "desc": "打开设置弹窗时自动请求 /api/environment，实时呈现 FFmpeg、FFprobe、Chrome、下载目录可写性、yt-dlp、Playwright 等基础依赖的健康状态红绿灯指示与指导建议。",
        "trigger": "打开「下载设置」弹窗时自动执行检查",
        "code": "app/environment.py: check_environment(); app/static/home.js: #environment-status",
        "status": "生产级就绪",
        "rules": "对于缺失的依赖给出具体的安装指导文案。"
    },
    {
        "id": "F-UI-024",
        "category": "前端交互与现代UI系统",
        "subsystem": "设置抽屉与配置管理",
        "name": "设置抽屉：Chrome 登录态开关与 Profile 设定",
        "desc": "提供「使用 Chrome 登录态」全局开关，需要登录的内容开启；支持可选填写 Chrome 个人资料文件夹（如 Default, Profile 1, Profile 2），精准读取对应多账号凭据。",
        "trigger": "设置面板中切换开关或输入个人资料名称并保存",
        "code": "app/static/home.js: #use-cookies, #profile; app/main.py: AppConfig",
        "status": "生产级就绪",
        "rules": "服务端对 profile 进行格式正则校验；保存时带有写入原子锁保护。"
    },
    {
        "id": "F-UI-025",
        "category": "前端交互与现代UI系统",
        "subsystem": "设置抽屉与配置管理",
        "name": "设置抽屉：视频号元宝登录态管理面板",
        "desc": "在设置弹窗中提供专用的「视频号登录态」折叠面板，清晰展示当前元宝 Cookie 配置状态，提供「扫码登录元宝」、「保存登录态」及「清除登录态」三大功能按钮。",
        "trigger": "设置弹窗中展开「视频号登录态」面板",
        "code": "app/static/home.js: .wechat-settings; app/wechat.py",
        "status": "生产级就绪",
        "rules": "输入框为密码类型，密文处理，绝不反显已保存的敏感 Cookie 内容。"
    },
    {
        "id": "F-UI-026",
        "category": "前端交互与现代UI系统",
        "subsystem": "设置抽屉与配置管理",
        "name": "设置抽屉：下载缓存容量精细管理与防护",
        "desc": "设置弹窗内展示当前缓存所占字节数与文件数，提供「清理缓存」操作。若当前存在未结束任务，直接拦截并提示 409；对暂停任务清理前弹出二次确认提醒。",
        "trigger": "设置弹窗中点击「清理缓存」",
        "code": "app/static/home.js: #clear-cache; app/main.py: /api/cache/clear",
        "status": "生产级就绪",
        "rules": "清理返回释放字节数与剩余文件数；清理仅作用于 cache 目录。"
    },
    {
        "id": "F-UI-027",
        "category": "前端交互与现代UI系统",
        "subsystem": "视觉设计与无障碍适配",
        "name": "浅色 / 深色双主题无缝切换",
        "desc": "顶部右侧提供主题切换按钮（日月图标），支持浅色与深色模式瞬时切换；整体重构两套配色方案，文字对比度实测达 4.7–17.1:1，完全满足 WCAG AA 级无障碍标准。",
        "trigger": "点击顶栏右上角的主题切换图标",
        "code": "app/static/home.js: #theme-toggle; app/static/home.css",
        "status": "生产级就绪",
        "rules": "主题选择持久化记录在 localStorage；尊重大操作系统 prefers-color-scheme。"
    },
    {
        "id": "F-UI-028",
        "category": "前端交互与现代UI系统",
        "subsystem": "视觉设计与无障碍适配",
        "name": "自适应响应式布局与防横向溢出",
        "desc": "采用现代 CSS Grid 与 Flexbox 重塑布局，在 740px、760px、1280px、1440px 等多分辨率视口下均保持良好的可用性，测试证实无任何横向溢出滚动条。",
        "trigger": "缩放窗口或在小屏设备上浏览界面",
        "code": "app/static/home.css: media queries",
        "status": "生产级就绪",
        "rules": "侧边栏与主工作区自适应伸缩；文字与表格自动换行。"
    },
    {
        "id": "F-UI-029",
        "category": "前端交互与现代UI系统",
        "subsystem": "视觉设计与无障碍适配",
        "name": "列表轮询刷新时的键盘焦点精确锁定",
        "desc": "前端定时轮询或 SSE 驱动任务列表重绘时，系统依据当前聚焦的任务 ID 与按钮动作精确还原 activeElement，彻底解决列表刷新把用户焦点丢回 body 的体验缺陷。",
        "trigger": "使用键盘 Tab 键选中任务列表内元素并保持时",
        "code": "app/static/home.js: restoreFocus()",
        "status": "生产级就绪",
        "rules": "全键盘操作无障碍无缝支持；长列表更新不产生焦点跳跃。"
    },
    {
        "id": "F-UI-030",
        "category": "前端交互与现代UI系统",
        "subsystem": "视觉设计与无障碍适配",
        "name": "轻量级异步 Toast 消息通知浮层",
        "desc": "在页面右下角/左下角提供无障碍 Toast 提醒容器（role=status, aria-live=polite），对复制成功、设置保存成功、网络恢复等关键动作提供平滑的无遮挡临时浮动提示。",
        "trigger": "执行剪贴板复制、保存设置或清除缓存成功时",
        "code": "app/static/home.js: showToast(); app/static/index.html: #toast-region",
        "status": "生产级就绪",
        "rules": "几秒后自动淡出消失；支持多条通知堆叠展现。"
    },
    {
        "id": "F-UI-031",
        "category": "前端交互与现代UI系统",
        "subsystem": "使用帮助与许可证声明",
        "name": "使用帮助指引与开源许可证说明弹窗",
        "desc": "顶栏提供帮助按钮，呼出「开始使用拾影」弹窗，提供 3 步新手上手图文教程（复制链接、解析下载、登录说明），明确标注版本号与 GPL-3.0 开源许可证条款。",
        "trigger": "点击顶栏右上角的使用帮助图标",
        "code": "app/static/home.js: #help-dialog; app/static/index.html",
        "status": "生产级就绪",
        "rules": "向用户阐明本地采集与合法使用原则；明确说明不含字幕/转写后续功能。"
    },
]

# Write to file
with open("features_data.py", "a", encoding="utf-8") as f:
    f.write("\nALL_FEATURES.extend(MGR_AND_UI_FEATURES)\n")
    f.write("print(f'Total features now: {len(ALL_FEATURES)}')\n")

print("Manager and UI features appended.")
