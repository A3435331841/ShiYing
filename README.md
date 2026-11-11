<div align="center">

# 拾影 · Shiying

**专业级多媒体本地高保真采集与归档引擎**

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/Framework-FastAPI-teal.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/Tests-1900%2B%20Passing-brightgreen.svg)](tests/)
[![Architecture](https://img.shields.io/badge/Architecture-SPI%20Modular-orange.svg)](docs/specs/平台接入架构规范与工程治理准则.md)

[功能特性](#-核心特性) • [平台矩阵](#-全平台支持矩阵) • [快速上手](#-快速上手) • [架构全景](#-系统架构) • [开发规范](#-开发与协作规范) • [更新日志](CHANGELOG.md)

</div>

---

## 📖 项目简介

**拾影（Shiying）** 是一款专注于**“本地化、高保真、零二压、去中心化”**的多媒体资源采集与归档工具。它在本地运行轻量 Web 服务，支持主流短视频、长视频、图集、Live Photo 实况照片及私有流媒体的批量解析与原画归档。

系统遵循**“严格不二次重编码、无损封装合并、实测物理参数优选”**原则，拒绝网页端清晰度标签欺骗，自动探测并保存平台当前所能分发的最高规格媒体流。

---

## ⚡ 核心特性

- 🎯 **真实天花板画质**：4K/8K 自动优选，拒绝虚假 2K/4K 标签，全链路通过 FFprobe 实测物理码率与实际分辨率。
- 🔄 **零二次重编码 (No Re-encoding)**：直接保存平台分发的原始字节；分离音频/视频轨仅通过 FFmpeg 进行无损封装合并（Remux）。
- 📸 **实况照片完备成对**：小红书、抖音 Live Photo 静态图与同名动态 MP4 独占锁成对落盘，绝不错位。
- 🛡️ **断点续传与自愈清理**：原生 HTTP Range 分块断续传输，416 异常自愈，首尾 64KB 极速哈希碰撞去重与秒传。
- 🔐 **硬件级安全防护**：Windows 本地凭据采用 DPAPI 硬件密钥隔离加密；SSRF 逐跳检查与 23 种可打开媒体白名单防投毒。
- 🖥️ **本地极简无感运行**：基于 Win32 Job Object 与 Mutex 单实例管理，双击即开，退出即销毁，杜绝后台僵尸进程。

---

## 🌐 全平台支持矩阵

| 平台 | 覆盖范围 | 核心逆向与处理技术 | 画质与特性支持 |
| :--- | :--- | :--- | :--- |
| **小红书** | 单视频 / 图文 / 实况 / 主页滚动 | Direct CDN 还原、`originVideoKey` 优先 | 原图 HEIC、无水印原画视频、Live Photo 配对 |
| **抖音** | 单视频 / 图集 / 实况 / 作者主页 | SecSDK a_bogus 签名、双源探测 (author-feed & amemv) | 2K/4K 原画实测、Live Photo 成对、去重防串号 |
| **抖音精选** | 知识合集 / 横屏长视频 / 专辑 | 独立 `/m/video` 路由展开、专有目录分治 | 独立归档至 `downloads/Selected-Douyin/` |
| **快手** | 单作品 / 短链 / 图集 / 作者主页 | Playwright CDP 被动网络监听、有界分页 | 横版图集、竖版 16 切片长图完整保留 |
| **哔哩哔哩** | 单P / 多P选集 (`?p=1`) / 专栏图文 | 动态 Opus 正文解析、选集批量前缀排号 | `bestvideo*+bestaudio` 无损 FFmpeg Remux |
| **YouTube** | 常规 / Shorts / 播放列表 / 频道 | Deno 沙箱驱动执行 n-sig 解密挑战 | 4K/8K 极限分离流提取、音视频合成 |
| **微信视频号** | 单作品私有流媒体 | 腾讯元宝 Session 桥接、ISAAC64 头部 128KB 动态解密 | DPAPI 凭据安全存储、解密流 FFprobe 校验 |

---

## 🚀 快速上手

### 环境准备
- **Python** 3.10 或更高版本
- **Google Chrome**（用于 Cookie 会话与风控探测）
- **FFmpeg / FFprobe**（加入系统 PATH，用于媒体无损封装与画质探测）

### Windows (推荐)
直接在项目根目录下双击运行：
- **`启动拾影.bat`**：启动应用并在浏览器自动打开 `http://127.0.0.1:8766`。
- **`停止拾影.bat`**：一键安全停机，平稳释放端口与互斥锁。

### macOS / Linux
```bash
# 启动
chmod +x scripts/unix/start.sh
./scripts/unix/start.sh

# 停止
./scripts/unix/stop.sh
```

---

## 🏗️ 系统架构

系统遵循 **Clean Architecture** 分层与 **SPI (Service Provider Interface)** 插件化设计：

```
[UI / Web 表现层] ──── HTTP / SSE ───► [FastAPI 调度路由 (app/main.py)]
                                                  │
                 ┌────────────────────────────────┴───────────────────────────────┐
                 ▼                                                                ▼
      [app/manager/ 任务状态中心]                                    [app/platforms/ 统一平台 SPI]
      ├─ core.py (任务增删改查)                                       ├─ base.py (PlatformProvider)
      ├─ lifecycle.py (10种状态变迁)                                   ├─ douyin / selected_douyin
      └─ queue.py (并发槽位调度)                                      ├─ xiaohongshu / kuaishou
                 │                                                    ├─ bilibili / youtube / wechat
                 └────────────────────────────────┬───────────────────┘
                                                  ▼
                                      [app/engine/ 下载传输引擎]
                                      ├─ transfer.py (Range断点续传/自愈重试)
                                      ├─ naming.py (跨平台路径清洗/180字节截断)
                                      └─ file_publish.py (首尾哈希去重/原子落盘)
```

---

## 📚 知识中心与规范导航

- 📘 [平台接入架构规范与工程治理准则](docs/specs/平台接入架构规范与工程治理准则.md)：系统设计哲学与 6 大 SPI 声明。
- 🔬 [媒体探测与画质保障技术白皮书](docs/specs/媒体探测与画质保障技术白皮书.md)：画质探测与反标签欺骗深度原理解析。
- 📊 [全系统功能与极端分支清单 (279 项)](docs/specs/拾影项目全功能矩阵清单.xlsx)：全量功能、15 类异常决策树与 API 字典。
- 📋 [本地文件全景审计与工程治理清单](docs/specs/本地文件全景审计与工程治理清单.md)：全量 1,399 项文件资产治理决策。
- 📖 [拾影操作使用说明书](docs/guides/拾影使用说明.md)：面向最终用户的图文使用指南。

---

## 🤝 开发者与社区规范

我们遵循现代顶级开源项目工程标准：
- **AI 智能体开发规范**：参阅 [AGENTS.md](AGENTS.md) 了解智能体行为防线与代码修改铁律。
- **开源贡献指南**：参阅 [CONTRIBUTING.md](CONTRIBUTING.md) 了解代码风格与 PR 提交规范。
- **安全漏洞披露**：参阅 [SECURITY.md](SECURITY.md) 提交私密漏洞报告。
- **变更记录**：完整历史演进记录请查阅 [CHANGELOG.md](CHANGELOG.md)。

---

## 📄 开源许可证

本项目核心代码基于 **[GPL-3.0-or-later](LICENSE)** 开源协议，引用的第三方兼容模块保留其原始协议（见 [NOTICE.md](NOTICE.md)）。
