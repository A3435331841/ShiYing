# -*- coding: utf-8 -*-
"""Full state machine and interaction details for UI components in Shiying."""

UI_STATE_MACHINE = [
    {
        "component": "链接输入框 (Input Box)",
        "scene": "用户直接粘贴包含杂乱文本的分享文案",
        "condition": "输入内容包含汉字、多行文本及包含 http(s):// 协议的链接",
        "action": "监听 paste / input 事件，调用前端正则与后端 extract_url 智能剥离杂文",
        "ui_response": "输入框内容瞬间自动替换为提纯后的标准链接；若包含非法字符则高亮红色微边框",
        "edge_case": "若用户同时粘贴多个链接，默认提取首个有效链接，并在输入框下方提示'已提取首个链接'"
    },
    {
        "component": "链接输入框 (Input Box)",
        "scene": "用户输入为空或纯空格",
        "condition": "点击提交按钮或按下回车键，但输入框长度为 0 或纯空白字符",
        "action": "拦截提交行为，不发起任何网络请求",
        "ui_response": "输入框触发摇晃动画 (Shake Effect)，边框红闪，并在下方展示'请输入有效的视频或图文链接'",
        "edge_case": "自动聚焦输入框 (input.focus())，方便用户直接重新键入"
    },
    {
        "component": "提交下载按钮 (Submit Button)",
        "scene": "正常解析并启动下载",
        "condition": "输入合法支持的 URL，网络连接处于 online 就绪态，且系统未处于全局繁忙状态",
        "action": "发送 POST /api/jobs，带 auto_start=true 与 force=false",
        "ui_response": "按钮变为'提交中...'并禁用，展示转圈加载动画；成功入队后清空输入框并跳转至【全部】任务列表",
        "edge_case": "请求超时（默认 20 秒）未返回时，弹出 Toast 提醒'请求超时，后台可能正在处理，请稍后刷新'"
    },
    {
        "component": "解析预览弹窗 (Preview Dialog)",
        "scene": "针对多P视频、合集或作者主页作品的手动勾选",
        "condition": "用户点击【解析预览】按钮，或提交了包含多子项的主页/播单链接",
        "action": "调用 POST /api/preview，启动最高 240 秒的长时探测，异步拉取所有单项元数据",
        "ui_response": "弹出居中模态弹窗，展示作品总数、作者头像、全部单项列表（含缩略图、标题、画质标签、时长）",
        "edge_case": "提供【全选】、【反选】、【仅选视频】、【仅选图片】快捷过滤按钮；单个作品画质支持下拉切换"
    },
    {
        "component": "重复任务确认弹窗 (Duplicate Modal)",
        "scene": "检测到已存在相同链接的任务",
        "condition": "POST /api/jobs 响应 409 或 GET /api/jobs/exists 返回 true（force=false）",
        "action": "中断直接创建流程，挂起当前请求，在页面中央激活覆盖层防误触弹窗",
        "ui_response": "弹窗提示'该作品已在下载列表中'，并展示原有任务的当前状态与进度",
        "edge_case": "弹窗提供两个按钮：1.【查看已有任务】（自动平滑滚动并高亮闪烁该卡片）；2.【仍然下载】（带 force=true 强制重新创建）"
    },
    {
        "component": "7大平台快捷图标栏 (Platform Bar)",
        "scene": "用户点击平台图标快速指引",
        "condition": "点击顶部抖音、快手、小红书、B站、YouTube、视频号图标",
        "action": "检查输入框当前状态：若为空，填入该平台支持的 URL 模式指引与示例；若处于任务管理视窗，快速过滤该平台任务",
        "ui_response": "图标触发缩放按压动效，被选中的平台图标添加发光高亮外边框",
        "edge_case": "再次点击已选中的平台图标，自动取消筛选，恢复展示全部平台任务"
    },
    {
        "component": "任务卡片状态面板 (Task Card)",
        "scene": "任务在 10 种状态机间的实时变迁呈现",
        "condition": "接收到 SSE event_stream 广播或列表定时刷新事件",
        "action": "根据 job.status 动态应用 .tone-active, .tone-success, .tone-warning, .tone-failed 样式类",
        "ui_response": "QUEUED(灰字排队) -> DISCOVERING(蓝字解析中) -> DOWNLOADING(动态百分比+速度MB/s) -> COMPLETED(亮绿完成) / FAILED(红字报警)",
        "edge_case": "下载中实时计算平滑移动平均下载速度与动态预估剩余时间 (ETA)"
    },
    {
        "component": "任务卡片操作按钮组 (Task Actions)",
        "scene": "针对运行中任务的干预控制",
        "condition": "任务处于 DOWNLOADING 状态",
        "action": "用户点击【暂停】图标",
        "ui_response": "向后端发送 POST /api/jobs/{id}/pause，按钮瞬间切换为【继续】图标，进度条定格，状态文字变更为'已暂停'",
        "edge_case": "若用户点击【取消】，弹出轻量二次确认气泡，确认后终止传输并保留已有 .part 缓存"
    },
    {
        "component": "任务卡片操作按钮组 (Task Actions)",
        "scene": "针对失败/异常任务的一键重试与诊断",
        "condition": "任务处于 FAILED, PARTIAL 或 NEEDS_AUTH 状态",
        "action": "展示红色/黄色警告图标，渲染中文错误诊断标签（如【滑块验证】、【访问限频】、【需要登录】）",
        "ui_response": "提供高亮主【重试】按钮；若为小红书滑块提供【打开Chrome验证】；若为多P图集支持单独点击失败子项的【重试】",
        "edge_case": "微信视频号旧任务若中断，提示'请重新提交链接'，按钮变更为【重新创建】"
    },
    {
        "component": "分类过滤与标签页切换 (Filter Tabs)",
        "scene": "用户在海量任务中按状态归类审阅",
        "condition": "点击【全部】、【下载中】、【已完成】、【需要处理】、【已暂停】Tab 标签",
        "action": "修改内部 state.filter，触发视图重绘，利用 CSS 伪类与虚拟列表瞬时完成显示/隐藏切换",
        "ui_response": "各 Tab 带有数字气泡徽标（Badge），如【下载中 (3)】、【需处理 (1)】，数量实时随状态同步增减",
        "edge_case": "切换 Tab 时保留用户的列表滚动高度，杜绝切换后滚动条跳回顶部的糟糕体验"
    },
    {
        "component": "实时模糊搜索框 (Search Box)",
        "scene": "按关键词精准定位特定作者或作品",
        "condition": "用户在任务列表搜索栏中输入汉字、拼音、英文或纯数字",
        "action": "监听 input 变化，对所有内存任务卡片的 title, author, media_id, url 字段进行大小写无关匹配",
        "ui_response": "任务卡片瞬间收缩过滤，非匹配项平滑隐藏；无匹配项时展示空状态插画'未找到相关任务'",
        "edge_case": "搜索框右侧提供一键清除 (X) 按钮，点击后秒级复原完整列表"
    },
    {
        "component": "内置媒体播放器与画廊 (Media Modal)",
        "scene": "下载完成后原地开箱验货",
        "condition": "在已完成任务卡片上点击【播放】图标",
        "action": "检测文件后缀：若为 MP4/WEBM/MOV 唤起轻量内置视频播放器；若为 WebP/JPG/PNG 启动图片灯箱画廊",
        "ui_response": "页面弹出黑色半透明画廊遮罩层，居中播放视频或展示大图，支持鼠标滚轮缩放、全屏切换",
        "edge_case": "支持键盘快捷键：空格键切换播放/暂停，左右方向键切换图集上一张/下一张，Esc 键秒退关闭"
    },
    {
        "component": "系统设置抽屉 (Settings Drawer)",
        "scene": "更改下载路径、配置 Cookie 与管理系统状态",
        "condition": "点击右上角设置齿轮按钮",
        "action": "从屏幕右侧滑出抽屉式配置面板，展示当前保存目录、Chrome Cookie 开关、元宝登录态、环境健康体检表",
        "ui_response": "修改下载目录提供【选择文件夹】系统对话框；修改设置后自动异步 PUT /api/config 保存生效",
        "edge_case": "如果检测到 config.json 曾损坏自愈，抽屉顶部弹出醒目黄色告警并提供原损坏文件备份路径"
    },
    {
        "component": "连接状态与服务心跳组件 (Connection Pill)",
        "scene": "系统底层心跳异常与断网恢复",
        "condition": "后端进程关闭、网络异常或系统休眠唤醒",
        "action": "SSE 连接触发 onerror，启动每隔 2 秒、4 秒、8 秒的指数退避重试探测",
        "ui_response": "右下角状态药丸瞬间从绿色'本地运行'变为红色'连接中断'，提交按钮置灰禁用；重连成功后瞬时变绿恢复",
        "edge_case": "重连建立后，自动拉取最新的全量 GET /api/jobs 数据，全量同步断网期间的任务状态变更"
    }
]

print(f"Loaded {len(UI_STATE_MACHINE)} UI state machine interaction items.")
