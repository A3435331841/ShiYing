# -*- coding: utf-8 -*-
"""Super-detailed micro-logic branches covering low-level OS, cryptography, and engine constraints."""

MICRO_ENGINE_AND_OS_LOGIC = [
    # =========================================================================
    # 密码学与本地机密保护 (Cryptography & DPAPI)
    # =========================================================================
    {
        "id": "DL-CRYPTO-001",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "Windows DPAPI 凭据硬件加密",
        "name": "CryptProtectData 静默硬件级凭据加密",
        "condition": "用户保存微信元宝 Cookie 至 data/yuanbao-cookie.txt",
        "logic": "调用 crypt32.dll CryptProtectData，传入 dwFlags=1 (CRYPTPROTECT_UI_FORBIDDEN) 禁止弹窗，绑定当前 Windows 账户主密钥加密",
        "code": "app/wechat_secret.py:_crypt:24",
        "result": "输出以 SHIYING-DPAPI-1\\n 为前缀的密文；成功加密后调用 kernel32.LocalFree 释放 native 内存",
        "safety": "严禁非 Windows 系统调用；密文复制到其他机器无法解密，彻底杜绝明文凭据泄露"
    },
    {
        "id": "DL-CRYPTO-002",
        "category": "安全防护、反风控与故障诊断",
        "subsystem": "Windows DPAPI 凭据硬件加密",
        "name": "旧版本明文凭证无缝就地升级与刷盘",
        "condition": "启动读取 data/yuanbao-cookie.txt 时，检测到文件存在但不以 SHIYING-DPAPI-1\\n 前缀开头（旧版本遗留）",
        "logic": "读取旧明文内容并 strip()；若有效则立即调用 save_secret 写入 .protected.tmp，执行 fsync 刷盘后原子覆盖",
        "code": "app/wechat_secret.py:load_secret:53",
        "result": "静默就地升级为 DPAPI 硬件加密密文，返回原明文供当前进程使用",
        "safety": "只在解密/加密成功后覆盖，升级失败不破坏原文件"
    },
    {
        "id": "DL-CRYPTO-003",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 ISAAC64 解密",
        "name": "ISAAC64 黄金比例常量与 4 轮混合初始化",
        "condition": "收到微信视频号有效的无符号 64 位 decodeKey 密钥",
        "logic": "使用黄金分割比例常量 0x9E3779B97F4A7C13 填充 8 个内部状态变量，将 seed[0] 设为 key，执行 4 轮深度位运算 _mix 混合",
        "code": "app/wechat_isaac.py:ISAAC64._init:22",
        "result": "初始化 256 项 64 位状态数组 mm，彻底打散密钥熵值",
        "safety": "所有加法与位移严格通过 & MASK64 限制在 64 位无符号整数内"
    },
    {
        "id": "DL-CRYPTO-004",
        "category": "平台接入与媒体解析",
        "subsystem": "微信视频号 ISAAC64 解密",
        "name": "头部 128KB (131072 字节) 大端按位异或解密",
        "condition": "下载视频号加密 MP4 媒体流数据",
        "logic": "限制 actual_enc_len = min(len(data), 131072)；每轮取 64 位伪随机数转为 8 字节大端流 struct.pack('>Q')，逐字节异或解密",
        "code": "app/wechat_isaac.py:decrypt_channels_data:138",
        "result": "仅解密前 128KB 头部，还原标准 MP4 moov/mdat 文件头；128KB 后明文直接拼接",
        "safety": "若 key==0 或 len==0 直接原样返回，耗时极短 (<20ms)"
    },

    # =========================================================================
    # 系统级进程树与 Windows 内核作业对象 (Win32 Job Object & Process Tree)
    # =========================================================================
    {
        "id": "DL-SYS-001",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "Win32 作业对象管理",
        "name": "JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE 进程树绑定",
        "condition": "在 Windows 系统上启动拾影主服务进程 (launcher.py)",
        "logic": "创建 Win32 Job Object，填充 JobObjectExtendedLimitInformation，设置 LimitFlags |= 0x2000 (KILL_ON_JOB_CLOSE)",
        "code": "launcher.py:_assign_windows_backend_job:124",
        "result": "将主进程与派生的所有子进程绑定至作业对象",
        "safety": "当主进程因崩溃、任务管理器结束或断电退出时，Windows 内核强制同步瞬间杀死所有子进程"
    },
    {
        "id": "DL-SYS-002",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "句柄安全继承",
        "name": "PROC_THREAD_ATTRIBUTE_HANDLE_LIST 白名单句柄继承",
        "condition": "主进程启动后端 Python 服务时需要传递项目锁句柄 (ProjectLock)",
        "logic": "设置 STARTUPINFOEX lpAttributeList，仅将 lock_fd 加入继承白名单；调用 set_project_lock_inheritable 临时置为可继承",
        "code": "launcher.py:_popen_with_project_lock:108",
        "result": "精准仅向子进程传递项目锁，严禁泄露其他套接字或敏感文件句柄",
        "safety": "在 finally 块中立即恢复 handle 的不可继承状态"
    },
    {
        "id": "DL-SYS-003",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "单实例锁与端口协调",
        "name": "ProjectLock 文件排他锁防多开冲突",
        "condition": "启动时尝试获取 data/runtime/project.lock 排他写锁",
        "logic": "Windows 下调用 msvcrt.locking(fd, LK_NBLCK)；POSIX 下调用 fcntl.flock(fd, LOCK_EX | LOCK_NB)",
        "code": "app/runtime.py:ProjectLock.acquire",
        "result": "锁已被持有时抛出 ProjectLockHeldError，新进程主动放弃启动并唤醒前台已有实例",
        "safety": "杜绝双进程同时写入同一数据库导致的脏读坏档"
    },
    {
        "id": "DL-SYS-004",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "平稳停机鉴权",
        "name": "stop.py 16KB 响应体截断与身份防误杀",
        "condition": "执行 python stop.py 停止运行中的拾影服务",
        "logic": "向 127.0.0.1:8766 发送探测请求；限制最大响应体不超过 16KB (16384 bytes)；校验返回 build_id 与项目根目录一致性",
        "code": "stop.py:_read_json_response:78",
        "result": "验证失败抛 StopRefusedError，拒绝误杀占用同端口的其他未知程序",
        "safety": "防御大文件内存溢出与恶意重定向"
    },
    {
        "id": "DL-SYS-005",
        "category": "桌面客户端集成与系统守护",
        "subsystem": "平稳停机鉴权",
        "name": "120 秒优雅停机等待与 5 秒强制强杀降级",
        "condition": "发送合法 X-Stop-Token 停止后端服务",
        "logic": "给后端预留最长 120 秒 (BACKEND_GRACEFUL_STOP_TIMEOUT_SECONDS) 允许正在落盘的任务平稳刷盘；超时若未退出，等待 5 秒强制 SIGKILL",
        "code": "launcher.py, stop.py",
        "result": "最大程度保全用户正在下载的文件不损坏，同时防止进程永远假死卡住",
        "safety": "退出后清理 data/runtime 锁文件与运行时记录"
    },

    # =========================================================================
    # 引擎底层传输规格与边界数字常量 (Engine Specs & Numerical Limits)
    # =========================================================================
    {
        "id": "DL-SPEC-001",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "字符串截断与容量限制",
        "name": "MAX_TITLE_BYTES (180 字节) 标题安全截断",
        "condition": "处理超长作品标题生成本地文件名",
        "logic": "将标题编码为 UTF-8 字节流，超过 180 字节则在字符边界处平滑截断，确保加上日期与 ID 后不超过 255 字节组件上限",
        "code": "app/engine/common.py:MAX_TITLE_BYTES:258",
        "result": "既保留标题辨识度，又保证生成的文件名在所有文件系统中均合法有效",
        "safety": "严格按照 Unicode 字符边界截断，杜绝产生非法 UTF-8 半字符乱码"
    },
    {
        "id": "DL-SPEC-002",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "字符串截断与容量限制",
        "name": "MAX_MEDIA_ID_BYTES (64 字节) 作品 ID 限制",
        "condition": "从 URL 或 JSON 元数据中提取作品唯一标识符",
        "logic": "强制限制作品 ID 字节长度不超 64 字节，过滤超出限制的异常串，全空则安全回退为 unknown-id",
        "code": "app/engine/common.py:MAX_MEDIA_ID_BYTES:261",
        "result": "保证文件命名格式 [ID] 结构稳健一致",
        "safety": "防止超长恶意伪造 ID 导致缓冲区溢出"
    },
    {
        "id": "DL-SPEC-003",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "探测与传输阶梯",
        "name": "抖音清晰度阶梯 (default, 4k, 2k, 1080p, 720p) 依次重试",
        "condition": "探测抖音作品高画质播放地址",
        "logic": "按照 DOUYIN_PROBE_RATIOS 定义的五级清晰度阶梯从高到低依次探测候选流，直到获取有效媒体流",
        "code": "app/engine/common.py:DOUYIN_PROBE_RATIOS:270",
        "result": "确保在任何网络环境下均能获取当前账号权限内可用的最高画质",
        "safety": "单一画质受限自动降级至下一级，不直接抛错中断"
    },
    {
        "id": "DL-SPEC-004",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "探测与传输阶梯",
        "name": "首包 256KB 范围探测与 64KB 流式写入分块",
        "condition": "发起媒体流探测与分段下载",
        "logic": "质量探测时仅拉取前 256KB (DOUYIN_PROBE_BYTES) 数据送入 FFprobe；正式下载时按 64KB (DOUYIN_STREAM_CHUNK_BYTES) 循环写入",
        "code": "app/engine/common.py:273, 276",
        "result": "探测阶段耗费极少流量 (<0.3MB)，正式下载兼顾内存占用与磁盘 I/O 吞吐",
        "safety": "内存开销完全恒定，百兆大文件零内存爆破"
    },
    {
        "id": "DL-SPEC-005",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "传输闲置与网络超时",
        "name": "120 秒双轨闲置超时 (Idle Timeout) 守护",
        "condition": "质量探测阶段或分段传输阶段连接假死无数据流出",
        "logic": "启动 DOUYIN_QUALITY_PROBE_IDLE_TIMEOUT_SECONDS 与 DOUYIN_TRANSFER_IDLE_TIMEOUT_SECONDS 双轨 120 秒超时计时",
        "code": "app/engine/common.py:306, 309",
        "result": "连续 120 秒未收到任何数据包立即主动掐断连接抛出超时异常，释放任务线程",
        "safety": "防止线程池所有并发槽位被卡死的连接耗尽"
    },
    {
        "id": "DL-SPEC-006",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "重定向安全与跳数限制",
        "name": "DOUYIN_MAX_MEDIA_REDIRECTS (5次) 最大重定向限制",
        "condition": "下载官方播放流经历 302 跨 CDN 线路重定向",
        "logic": "跟踪跟踪重定向跳数，逐跳校验 Host 是否在 DOUYIN_REGIONAL_MEDIA_DOMAINS 白名单内，且跳数严格 ≤ 5",
        "code": "app/engine/common.py:339",
        "result": "合法跳数内完成寻址；超过 5 跳或跳至未知域名立即抛 _DouyinRedirectRejected 终止",
        "safety": "彻底防御恶意无限重定向循环攻击 (Infinite Redirect Loop)"
    },
    {
        "id": "DL-SPEC-007",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "单作品时长上限",
        "name": "DOUYIN_MAX_DURATION_MS (7天) 异常时长防溢出拦截",
        "condition": "API 返回作品声明时长 duration_ms",
        "logic": "校验时长必须 ≤ 7 * 24 * 60 * 60 * 1000 毫秒（7天）；超出则判定为直播流或异常脏数据拒绝处理",
        "code": "app/engine/common.py:342",
        "result": "拦截无限时长流，避免生成无限写入的大文件挤爆磁盘",
        "safety": "仅允许有限长度的点播作品"
    },
    {
        "id": "DL-SPEC-008",
        "category": "平台接入与媒体解析",
        "subsystem": "官方媒体白名单域名",
        "name": "抖音直系与区域媒体域名严格白名单",
        "condition": "解析出的媒体流准备建立连接下载",
        "logic": "校验 Host 必须严格属于 douyin.com, douyinvod.com, amemv.com, zjcdn.com, douyincdn.com, idouyinvod.com, pstatp.com",
        "code": "app/engine/common.py:DOUYIN_REGIONAL_MEDIA_DOMAINS:353",
        "result": "白名单内官方 CDN 放行；非白名单直接拒绝，判定为非信任伪造地址",
        "safety": "防止 DNS 投毒与钓鱼下载注入"
    },
    {
        "id": "DL-SPEC-009",
        "category": "平台接入与媒体解析",
        "subsystem": "移动端专属 UA 伪装",
        "name": "Android 14 Pixel 8 Pro 移动端高优先级 UA 注入",
        "condition": "请求抖音媒体流与官方详情接口",
        "logic": "请求头注入 DOUYIN_MEDIA_HEADERS，伪装为 Linux; Android 14; Pixel 8 Pro Chrome/131.0 Mobile Safari/537.36",
        "code": "app/engine/common.py:DOUYIN_MEDIA_HEADERS:379",
        "result": "获得移动端专享的高清无水印视频分发流，显著降低风控拦截概率",
        "safety": "携带 Referer: https://www.douyin.com/ 保持防盗链合规"
    },
    {
        "id": "DL-SPEC-010",
        "category": "下载核心引擎与媒体管线",
        "subsystem": "FFprobe 探测安全边界",
        "name": "FFprobe 管道 3秒 / 文件 15秒 超时与 8GB 上限防护",
        "condition": "调用本地 FFprobe 探测流规格或完整文件规格",
        "logic": "管道探测设置 3.0 秒超时；文件探测设置 15.0 秒超时；限制探测文件最大不超 8GB (DOUYIN_MAX_PROBE_FILE_BYTES)",
        "code": "app/engine/common.py:324, 327, 330",
        "result": "超时未退出立即发送 kill() 杀死子进程，抛出 MediaDownloadError",
        "safety": "防止坏文件导致 FFprobe 解析卡死在后台死循环"
    }
]

print(f"Loaded {len(MICRO_ENGINE_AND_OS_LOGIC)} micro-engine and OS logic items.")
