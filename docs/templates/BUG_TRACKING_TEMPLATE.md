# 日常缺陷与安全漏洞追踪记录模板 (Bug & Vulnerability Tracking)

> 适用场景：日常运行、单元测试、真机冒烟、用户反馈或安全审计中发现的任何代码缺陷、逻辑异常、崩溃或安全隐患。  
> 归档命名建议：`docs/records/bugs/BUG-{YYYYMMDD}-{平台/模块}-{序号}.md`

---

## 1. 基本信息与影响评级

| 字段 | 说明 / 填报内容 | 示例参考 |
| :--- | :--- | :--- |
| **缺陷编号 (Bug ID)** | `BUG-{YYYYMMDD}-{模块简写}-{序号}` | `BUG-20261111-DY-001` |
| **发现日期** | YYYY-MM-DD | 2026-11-11 |
| **所属模块 / 平台** | `app/engine`, `app/manager`, `app/platforms/<platform>`, `UI` | 抖音 (Douyin) / 媒体流优选 |
| **严重等级 (Severity)** | **P0 (阻断/致命)** / **P1 (严重)** / **P2 (一般)** / **P3 (轻微)** | P1 (严重) |
| **安全敏感度 (Security)** | **公开** / **高危 (涉及加密、Token、本地执行安全)** | 高危 (涉及 URL 防穿透与 SSRF) |
| **当前状态** | 待排查 / 修复中 / 待验证 / 已关闭 / 挂起 | 修复中 |
| **关联 Issue / 提交** | GitHub Issue 或分支 SHA | `#42 / main:714d783` |

---

## 2. 缺陷现象与复现条件

### 2.1 现象简述
*清晰用一两句话描述出现了什么异常现象（如：下载特定长视频时报错 403，或文件名包含特殊 Unicode 时文件损坏）。*

### 2.2 触发前置条件
1. **操作系统与环境**：Windows 11 / Python 3.10 / Chrome 130
2. **账号与登录状态**：已登录 Cookie / 匿名游客状态
3. **输入 URL 或参数**：`https://...` (注意：若涉及私有 Token 或 Cookie 必须正则脱敏)

### 2.3 稳定复现步骤
1. 第一步：在主界面输入栏粘贴目标 URL；
2. 第二步：点击“开始解析”，观察控制台抓包输出；
3. 第三步：进入下载阶段，传输达到 99% 时触发重试；
4. 预期结果：解析并合成 4K 视频，提示下载完成；
5. 实际结果：抛出 `DownloadError: fragment 4 checksum mismatch`，状态流转为 `FAILED`。

### 2.4 现场异常堆栈与关键日志 (严格脱敏)
```log
[2026-11-11 10:24:18] [ERROR] [app.engine.transfer] Asset probe failed for url: https://v26-web.douyinvod.com/...
Traceback (most recent call last):
  File "D:\Shiying\app\engine\transfer.py", line 142, in _download_first_available_asset
    ...
```

---

## 3. 根本原因分析 (Root Cause Analysis, RCA)

- **直接诱因**：上游 CDN 在分块 Range 请求时返回了压缩编码头，导致 Content-Length 动态漂移。
- **深层架构原因**：传输引擎在检测网络断点时未处理 gzip/br 的透明解压流，引发本地写入字节与服务器报告大小不匹配。

---

## 4. 修复方案与安全边界

- [ ] **代码修改范围**：修改 `app/engine/transfer.py` 中的 Range 协商头。
- [ ] **回归防护机制**：在 `tests/test_downloader.py` 中补充模拟 CDN Content-Length 畸变用例。
- [ ] **安全审计项**：是否会引入二次重定向或本地文件覆写风险？已验证，落盘路径始终受 `publish_no_replace` 保护。

---

## 5. 验收标准与验证结论

- [ ] 单元测试是否 100% 通过（`pytest tests/` 无失败）；
- [ ] 现场实测用例覆盖验证（附验证结果截图或日志摘要）；
- [ ] 验收人签字 / 确认合入 Commit SHA。
