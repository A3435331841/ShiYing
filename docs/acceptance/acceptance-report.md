# 拾影验收报告（2026-11-23，含 2026-11-24 v0.4.0 复核）

> 对应任务：原任务系统路径 `.codeartsdoer/specs/shiying_downloader/tasks.md`（属仓库外任务系统，未随本仓库交付，无法就地复核）
> 结论口径：只记录本仓库实际执行并通过的检查；外部真实平台验收未完成时明确标注。
> 2026-11-24 当前交付结论见第 5 节；第 1–4 节保留当日历史快照，不用旧用例数代替当前回归。

## 1. 2026-11-23 历史自动化回归

| 检查 | 结果 |
|---|---|
| 基线 `pytest tests -q` | 2194 通过 / 25 跳过，189.4s |
| 最终 `pytest tests -q --tb=line --disable-warnings -p no:cacheprovider` | **截至 2026-11-23 的历史结果：全部通过，0 失败**；对应的是当日**未提交的工作树快照**，不代表已提交代码。已提交的 `main@e9fbd74` 最新 CI（run 44636157985）为 failure：`1 failed, 2162 passed, 31 skipped`（`test_pause_keeps_the_in_flight_item_resumable`）。本报告各项均为当日快照，代码改动后须按本行命令重跑确认 |
| 新增索引/能力测试 | `tests/test_index.py`、`tests/test_capabilities.py`、`tests/test_library_api.py` 现收集 9+7+3=19 项（原记 15 项，已按当前工作树更正并实跑通过） |
| 新增端点测试 | `tests/test_spec_endpoints.py` 现收集 8 项（原记 6 项，已更正并实跑通过） |
| 前端脚本语法 | `node --check app/static/home.js` 通过 |
| 前端行为测试 | `tests/test_frontend.py`、`tests/test_batch_frontend.py` 通过 |

## 2. 已完成能力

| 任务 | 实现 |
|---|---|
| 2.1–2.3 | `app/index/` 索引后端、FTS5 Sidecar、增量刷新、损坏/版本不符回退与后台重建 |
| 2.4 | `AppConfig.index_enabled`、`capabilities_path`，旧配置不改写即可加载 |
| 2.5–2.7 | `data/capabilities.json`、`app/capabilities.py`、`docs/acceptance/` 证据模板 |
| 2.8 | 索引与能力单测 |
| 3.1 | `GET /api/jobs?view=library` 游标分页、`index_status`、limit 校验 |
| 3.2 | `GET /api/events?since=` 先推 `snapshot`，快照与订阅同读锁 |
| 3.3 | `POST /api/cache/clear` 返回 `bytes_before` / `bytes_after` |
| 3.4 | `GET /api/jobs/{id}/export` 白名单诊断包，剥离 query 与绝对路径 |
| 3.5 | `POST /api/jobs` 追加 `ignored_text_count` / `unrecognized_samples` / `duplicates` |
| 3.6 | `GET /api/platforms` 返回三态能力与证据缺失标记 |
| 3.7 | `GET /api/environment` 追加 `capabilities_impact` |
| 3.8 | experimental 未确认 409、unavailable 422（微信元宝通道保持 V1 可用） |
| 4.3 | 前端 SSE 断线后携带最大 revision 重连并合并 snapshot |

## 3. 未完成 / 未验证

| 任务 | 状态 |
|---|---|
| 4.1 资料库前端改走索引分页 | **已完成**：资料库改走 `GET /api/jobs?view=library`（`limit=50`、`cursor`/`next_cursor`、`index_status`），筛选与搜索下推后端，支持按游标“加载更多”与滚动加载；微信记录单独补齐 |
| 4.2 平台状态标识派生渲染 | **已完成**：前端拉取 `/api/platforms`，按 supported/experimental/unavailable 与 evidence_missing 派生平台标签、角标与提示；静态 chip 仅作无网络兜底 |
| 4.4 依赖缺失禁用入口 | **未完成** |
| 4.5 粘贴统计 UI 展示 | **已完成**：提交后在 `#paste-stats` 展示后端返回的 `ignored_text_count` / `unrecognized_samples` / `duplicates`，并把原文案行随请求下推以产生真实统计 |
| 5.2 1000 任务性能实测 | **未执行** |
| 5.4 单实例/安全停机回归 | **未单独执行**（全量测试覆盖相关模块） |
| 5.6 快手真实作品验收 | **未完成**：缺登录态与真实作品 |
| 5.7 小红书真实作品验收 | **未完成**：缺登录态与真实作品 |
| 5.8 视频号元宝路径验收 | **未完成**：缺用户自身元宝登录态 |
| 2.9 索引健康检查脚本 | 可选，未实现 |

## 4. 已知设计取舍

- FTS5 `unicode61` 对中文整串分词，中文查询自动回退 LIKE；拉丁词走 FTS，0 命中时回退 LIKE。
- 损坏索引首次请求返回 `index_status=fallback`，后台线程重建后恢复 `ready`。
- 能力状态不因证据文件缺失而自动升降，只标记 `evidence_missing=true`。
- 微信 `confirm_required=false` 以保留 V1 创建路径；客户端抓取通道的 422 需显式通道参数，本期未接入。

## 5. 2026-11-24 v0.4.0 当前交付复核

### 5.1 自动化与缺陷修复

| 检查 | 当前结果 |
|---|---|
| `.venv/Scripts/python.exe -m pytest tests -q --tb=line --disable-warnings -p no:cacheprovider` | **2245 通过 / 25 跳过 / 0 失败**，共 2270 项 |
| 七平台 Provider SPI | 抖音、抖音精选、小红书、快手、B站、YouTube、视频号均已建立 Provider 子包并由 `registry.py` 统一注册；预览与正式任务发现走同一 SPI 委托链路 |
| 并发传输 | 修复不同任务共用 `.part` / 断点 JSON 的风险；临时路径按任务与作品隔离，内核锁移至运行态目录，下载目录不再残留 `.lock` |
| 断点持久化 | 修复检查点可能先于媒体字节落盘的问题；媒体先 `flush + fsync`，断点 JSON 再 `fsync` 并原子替换 |
| 冻结包与源码包 | 补入 `data/capabilities.json`；冻结模式从 `_internal/data` 回退，设置保存与能力加载使用同一内置文件；源码包继续排除 `data/` 下的运行态数据 |
| 发布工具 | 修复 FFmpeg `--repin` 空哈希比较；补充对应回归 |
| 预览一致性 | YouTube 预览与正式任务创建均不强制使用 Chrome Cookie，其余平台仍遵循全局开关 |
| 独立只读复核 | 三轮审查发现的问题均已修复；最后一轮未发现明确可复现的 release-blocking / major 缺陷 |

### 5.2 Windows 真机运行契约

执行 `scripts/dev/runtime_contract_smoke.py`，六项全部通过；本机证据位于被忽略的 `data/acceptance-artifacts/runtime-contract-smoke.json`。

| 检查 | 实测结果 |
|---|---|
| 真实 B站 CDN Range / If-Range | 首段与匹配校验器均返回 206 / 1024 字节；失效 `If-Range` 仍返回 206，但返回校验器不同，应用会拒绝复用旧断点 |
| 真实 FFmpeg HLS 停滞 | 本地播放清单的分段故意停滞，1.094 秒后有界失败，未残留 `.part` |
| WebView2 窗口关闭 | 实际窗口收到 `WM_CLOSE` 后关闭，后端 PID 保持不变 |
| 父进程监控 | 受监控父进程退出后，后端未存活 |
| Windows Job Object | 父进程退出并关闭作业对象后，后端未存活 |
| 关闭父进程监控 | 后端按设计存活；随后仅终止本次验收拥有的测试 PID |

### 5.3 实际 release 包核验

对生成的 `releases/拾影_v0.4.0_win64_portable_20261124.zip` 解压后执行包内 EXE，而非只检查构建日志。

| 检查 | 实测结果 |
|---|---|
| ZIP / SHA256 | 191,744,975 字节，329 个条目；SHA256 `9a0aaa7345e33c75a35c077b4f3b3ab96214a85778dd93a3e0e240c6d6a8102c`，与 `SHA256SUMS.txt` 一致 |
| 包内文件 | 主 EXE、停止 EXE、静态页面、能力登记、FFmpeg、FFprobe、Deno 均存在；无绝对路径或 `..` 条目 |
| 内置工具 | FFmpeg / FFprobe 9.0.2、Deno 2.9.8 均从包内实际执行成功 |
| 冻结后端 | 实际启动 `/api/health`，版本 0.4.0、build `7852e7ed64ca`，`restart_required=false` |
| 七平台能力 | 七个默认平台均登记且非 unavailable；微信未启用的客户端抓取通道仍按设计 unavailable |
| 设置与页面 | 实际 `PUT /api/config` 保存成功，包内生成 `data/config.json`；首页返回 200 |
| 安全停机 | 包内「停止拾影.exe」按运行身份停止本次验收后端 |
| 源码包 | `releases/拾影_v0.4.0_源码_20261124.zip` 包含七平台 Provider、测试与文档；仅放行 `data/capabilities.json`，不包含配置、任务记录、下载内容或虚拟环境；哈希列入 `SHA256SUMS.txt` |

本机证据位于被忽略的 `data/acceptance-artifacts/release-package-smoke.json`。

### 5.4 仍未消除的非阻断限制

- Provider 契约包、统一注册和生产委托已完成，但平台解析、签名、探测与解密本体尚未全部物理迁入各平台子包。
- 未做物理断电恢复测试；检查点持久化顺序有自动化覆盖，但不能替代电源故障验证。
- 真实 CDN 的失效 `If-Range` 存在非标准 206 行为；当前以校验器一致性防御，不宣称所有 CDN 都遵守标准语义。
- 全平台真实下载矩阵仍受登录态、私密内容、风控与链接有效期影响；快手、小红书、视频号等状态以使用说明的“平台实际范围”为准。
- 1000 任务性能实测、依赖缺失时前端入口禁用、微信客户端抓取通道仍未完成，属于已有非阻断范围，不在本次发布中宣称完成。