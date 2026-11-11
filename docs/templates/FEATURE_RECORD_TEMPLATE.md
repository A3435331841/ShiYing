# 功能设计与实现交付记录模板 (Feature & Implementation Record)

> 适用场景：新平台接入、核心架构重构、全新功能模块开发（如 YouTube 4K 支持、Deno 沙箱集成、精选独立分治等）。  
> 归档命名建议：`docs/records/features/FEAT-{YYYYMMDD}-{功能名称}.md`

---

## 1. 功能基本档案

| 字段 | 填报内容 | 示例 |
| :--- | :--- | :--- |
| **功能编号 (Feature ID)** | `FEAT-{YYYYMMDD}-{模块}-{序号}` | `FEAT-20261111-YT-001` |
| **功能全称** | 一句话概括该功能的核心价值 | YouTube 4K/8K 分离流音视频提取与 Deno 沙箱集成 |
| **所属业务域** | `Platforms` / `Engine` / `UI` / `OS Integration` | `Platforms / YouTube` |
| **遵循设计规范** | 指向的架构规范章节 | 遵循 `docs/specs/平台接入架构规范与工程治理准则.md` |
| **交付版本** | 目标系统版本号 | v0.4.0 |
| **交付日期** | YYYY-MM-DD | 2026-11-11 |

---

## 2. 需求背景与功能规格

### 2.1 需求价值
- 为什么系统需要增加这项能力？
- 解决了哪些用户在资源采集或媒体归档上的刚需？

### 2.2 详细功能规格清单 (Functional Specs)
1. **输入能力**：
   - 支持的标准 URL 格式（单视频、Shorts、Playlist、Channel 主页）；
   - 支持的参数形态（包含带 `&list=` 的单视频场景识别）。
2. **处理流程**：
   - 提取媒体元数据及流清单；
   - 调度外部沙箱（Deno）执行 JS challenge 解密 n-sig；
   - 探测最优视频轨与最优音频轨；
   - 执行 FFmpeg 无损混流 (Copy Remux)。
3. **输出规格**：
   - 落盘标准目录：`downloads/YouTube/{Author}/{YYYY-MM-DD}-{Title} [{ID}].mp4`；
   - 元数据写入与缩略图内嵌规范。

---

## 3. 架构设计与实现拓扑

### 3.1 核心类与时序交互
```
[Client / UI] 
      │ 提交 URL
      ▼
[Identify Layer] ──识别为 Platform.YOUTUBE──► [YouTubeProvider]
                                                      │ 1. discover()
                                                      │ 2. probe_streams()
                                                      ▼
                                              [Deno Sandbox Execution] (解密 n-sig)
                                                      │
                                                      ▼
[Download Pipeline] ◄───返回最清晰音视频分离流───────┘
      │
      ▼ (下载后调度)
[FFmpeg Remuxer] ──合成单文件──► [File Publisher] (首尾哈希防重落盘)
```

### 3.2 涉及文件清单
- **新增模块**：
  - `app/platforms/youtube/provider.py`
  - `app/platforms/youtube/deno_runner.py`
  - `tests/test_youtube_provider.py`
- **改造模块**：
  - `app/platforms.py`（路由分发收拢）
  - `app/models.py`（平台字典与能力注册）

---

## 4. 边界分支与极限情况设计 (Edge Cases)

| 场景序号 | 极端边界情况 | 系统应对与降级策略 | 对应状态机 / 错误码 |
| :--- | :--- | :--- | :--- |
| **E1** | 用户未安装 Deno 运行时 | 提示“Deno 未就绪”，降级提取 1080P 或基础渐进式 MP4 流 | `SiteIssueCode.TOOL_MISSING` |
| **E2** | 遇到年龄限制或会员专属视频 | 提示需配置 Chrome 登录态 Cookie | `SiteIssueCode.NEEDS_LOGIN` |
| **E3** | 视频包含多语种音轨 | 默认提取原始语种/系统匹配第一主音轨，并记录元数据 | `ItemStatus.COMPLETED` |
| **E4** | 播放列表超过 1000 个视频 | 启用流式批量分页发现，每次 50 项，支持中断保护 | `SourceKind.PLAYLIST` |

---

## 5. 验收测试矩阵与交付检查表

- [ ] **接口契约验证**：严格实现 `PlatformProvider` 声明的六大生命周期方法；
- [ ] **自动化测试覆盖**：新增单元测试覆盖常规解析、异常降级、Deno 缺失等分支；
- [ ] **全量回归无损**：全系统原有 1900+ 单元测试 100% 保持绿色通过；
- [ ] **文档与矩阵同步**：在 `拾影项目全功能矩阵清单.xlsx` 中登记新增的功能条目与边界规则。
