<div align="center">

# 拾影 · Shiying

**在本机运行的 Web 媒体下载与整理工具**

[![Python Version](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-green.svg)](LICENSE)
[![FastAPI](https://img.shields.io/badge/Framework-FastAPI-teal.svg)](https://fastapi.tiangolo.com/)
[![Tests](https://img.shields.io/badge/Tests-1100%2B%20Passing-brightgreen.svg)](tests/)

[功能特性](#-功能特性) • [支持平台](#-支持平台) • [快速上手](#-快速上手) • [代码架构](#-代码架构) • [使用说明](docs/guides/拾影使用说明.md) • [许可证](LICENSE)

</div>

---

## 📖 项目简介

**拾影（Shiying）** 是一个在本地运行的 Web 工具，默认监听 `http://127.0.0.1:8766`。粘贴主流平台的内容链接，自动解析并把视频、图集、实况照片下载到本机。

它遵循几个基本原则：
- **不重编码**：直接保存平台分发的原始字节；音视频分离的流仅用 FFmpeg 做无损封装合并（remux），不降低画质。
- **自动探测最高可用画质**：绕过网页端的低清占位，优先获取平台当前提供的最高规格流。
- **结构化归档**：按作者建立文件夹，文件名以发布日期开头，多图与实况照片自动排号。
- **单机完全受控**：任务状态与下载内容全在本地，不上传云端，不收集用户数据。

---

## ⚡ 功能特性

- **原画下载**：优先使用原图与原画视频流，拒绝网页端低码率转码；音视频分离轨通过 FFmpeg 无损合并。
- **实况照片配对**：小红书、抖音的 Live Photo 静态图与同名动态 MP4 保证同一 stem 成对落盘，避免错位或丢动态轨。
- **断点续传与内容去重**：支持 HTTP Range 分块断点续传；本地文件存在时做分块全字节比对，内容相同安全复用，不同则自动递增编号，不盲目覆盖。
- **本地凭据保护**：Windows 下的敏感配置调用系统 DPAPI 保护；打开文件做后缀白名单与路径校验。
- **可靠的单实例与启停**：通过 Windows 互斥锁（Mutex）与进程看门狗保证单实例运行；退出时安全回收子进程，不遗留后台孤儿。
- **清晰的错误提示**：明确区分限流、验证码、登录失效、接口变动与网络错误，不把所有失败都笼统报成「下载失败」。

---

## 🌐 支持平台

| 平台 | 支持内容 | 关键处理逻辑 | 说明 |
| :--- | :--- | :--- | :--- |
| **小红书** | 单视频 / 图文 / 实况 / 主页滚动 | 优先 direct CDN 与 `originVideoKey` 原视频流 | 支持原图 HEIC，实况静态+动态配对 |
| **抖音** | 单视频 / 图集 / 实况 / 作者主页 | 双源探测（author-feed 与 amemv），本地浏览器辅助签名 | 2K/4K 选流，实况配对，主页防串号 |
| **抖音精选** | 横屏长视频 / 知识合集 / 专辑 | 独立 `/m/video` 路由解析 | 独立保存至 `downloads/Selected-Douyin/` |
| **快手** | 单作品 / 短链接 / 图集 / 作者主页 | Playwright CDP 监听真实网络响应 | 完整保留横版图集与竖版切片图 |
| **哔哩哔哩** | 单 P / 多 P 选集 / 专栏图文 | 动态 Opus 解析，多 P 批量排号 | 音视频轨走 `bestvideo+bestaudio` 无损合并 |
| **YouTube** | 常规视频 / Shorts / 播放列表 / 频道 | 基于 yt-dlp 与 Deno 执行签名挑战 | 提取高分辨率分离流并合成 |
| **微信视频号** | 单作品私有流 | 本地会话桥接，头部 ISAAC64 解密 | 凭据 DPAPI 加密，解密流经 FFprobe 校验 |

---

## 🚀 快速上手

### 环境准备
- **Python** 3.10 或更高版本
- **Google Chrome**（用于辅助会话与必要时的风控验证）
- **FFmpeg / FFprobe**（加入系统 PATH，用于音视频合并与画质探测）

### Windows（推荐）
在项目根目录下双击运行：
- **`启动拾影.bat`**：启动服务并在默认浏览器打开 `http://127.0.0.1:8766`。
- **`停止拾影.bat`**：安全停机，释放端口与互斥锁。

### macOS / Linux
```bash
# 启动
chmod +x scripts/unix/start.sh
./scripts/unix/start.sh

# 停止
./scripts/unix/stop.sh
```

---

## 🏗️ 代码架构

代码分层保持清晰，各司其职：

```
[前端界面 / Web UI] ─── HTTP / SSE ───► [FastAPI 路由 (app/main.py)]
                                                  │
                 ┌────────────────────────────────┴──────────────────────────────┐
                 ▼                                                               ▼
      [app/manager/ 任务状态中心]                                    [平台接入模块]
      ├─ core.py (任务增删改查)                                       ├─ platforms.py (统一平台元数据与分发)
      ├─ lifecycle.py (状态变迁与流转)                                ├─ xiaohongshu.py / douyin.py
      ├─ douyin_profile.py (抖音主页分页)                             ├─ kuaishou.py / bilibili_opus.py
      └─ xiaohongshu.py (小红书主页滚动)                              └─ wechat.py / wechat_isaac.py
                 │                                                               │
                 └────────────────────────────────┬──────────────────────────────┘
                                                  ▼
                                      [app/engine/ 下载传输引擎]
                                      ├─ core.py (MediaDownloader 门面与调度)
                                      ├─ transfer.py (Range 分块下载与网络重试)
                                      ├─ naming.py (跨平台文件名清洗与长度截断)
                                      └─ file_publish.py (全字节比对去重与原子落盘)
```

兼容说明：`app/downloader.py` 和 `app/task_manager.py` 保留为兼容门面，旧的导入通路和测试调用不受影响。

---

## 📚 开发文档

- [平台接入架构规范与工程治理准则](docs/specs/平台接入架构规范与工程治理准则.md)：架构约定与平台接入规范。
- [媒体探测与画质保障技术白皮书](docs/specs/媒体探测与画质保障技术白皮书.md)：画质探测与多流选优原理。
- [全系统功能矩阵清单](docs/specs/拾影项目全功能矩阵清单.xlsx)：功能分类与异常处理表。
- [本地文件全景审计清单](docs/specs/本地文件全景审计与工程治理清单.md)：仓库文件分布与归档说明。
- [使用说明书](docs/guides/拾影使用说明.md)：面向用户的操作指南。

---

## 📄 开源许可证

本项目核心代码采用 **[GPL-3.0-or-later](LICENSE)** 许可；引用的第三方兼容模块保留其原始协议，详情见 [NOTICE.md](NOTICE.md)。
