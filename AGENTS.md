# 拾影（Shiying）智能体协作与工程准则 (AGENTS.md)

> 本规范专门面向协助、重构、审查或维护拾影项目的 **AI 智能体（包括但不限于 Claude Code、OpenAI Codex、Cursor、Copilot、Windsurf 等）**。  
> 任何智能体在执行任何代码读取、分析、重构、生成或提交任务时，**必须严格且无条件遵守本文件所规定的所有准则**。

---

## 1. 核心架构防线与职责边界 (Architectural Invariants)

1. **严格遵循 SPI 插件契约**：
   - 核心下载引擎 (`app/engine/`)、调度器 (`app/manager/`) 与入口服务 (`app/main.py`) 严禁出现特定平台的逆向硬编码业务逻辑。
   - 所有平台的解析、鉴权、探测、解密必须封装在 `app/platforms/<platform>/` 独立包中，且必须实现 `PlatformProvider` 声明的 6 大标准生命周期接口。
2. **严禁破坏公共兼容门面**：
   - `app/downloader.py` 与 `app/task_manager.py` 是 v0.3.0 重构后为保证旧测试与旧 API 无缝运行的兼容门面 (Facade)。智能体在优化底层时，必须保留这两个门面的导入通路与接口签名，禁止直接物理删除。
3. **抖音与抖音精选物理分治**：
   - 抖音主站 (`douyin`) 与抖音精选 (`selected-douyin`) 拥有独立的平台枚举与落盘目录。智能体严禁将抖音精选强制重写合并进抖音主站。

---

## 2. 安全合规与隐私红线 (Security Redlines)

任何违反以下红线的行为均被视作重大破坏性事故：

- 🔴 **绝对凭据脱敏**：任何代码、测试输出、报错异常或日常日志中，**绝对禁止打印、记录、持久化或外泄任何 Cookie 明文、xsec_token、SessionID、Token 密钥**。所有向外暴露的异常堆栈必须经由 `safe_external_error_message()` 强制脱敏。
- 🔴 **受保护私钥绝不触碰**：`extension/yuanbao-login-copy.pem` 为本地私钥文件，**绝对禁止打开读取、复制、修改或提交至 git 仓库**。
- 🔴 **只读受控目录绝对锁定**：`backups/`（冷备份目录）与 `graphify-out/`（知识图谱产物目录）为严格只读保护目录，智能体**绝对不得修改、覆盖或删除其中的任何文件**。
- 🔴 **个人数据与运行态隔离**：`data/`、`cache/`、`downloads/`、`.claude/`、`.zcode/`、`artifacts/`、`审计产物/` 等本地运行时数据与工具痕迹**绝对禁止暂存或推送到公共远程仓库**。
- 🔴 **开源许可证红线**：整仓遵循 **GPL-3.0-or-later** 协议，智能体在引入外部依赖或代码段时，必须核实协议兼容性，严禁引入闭源或与 GPL 冲突的第三方代码。

---

## 3. 代码注释与文档化铁律 (Documentation & Docstring Standard)

智能体编写或修改代码时，必须遵循工业级 **PEP 257 / Google Style** 注释标准，禁止提交无注释的裸代码：

1. **模块级 Docstring**：每一个 `.py` 文件的最顶部必须包含三引号 docstring，清晰阐述：
   - 本模块的核心职责与业务边界；
   - 是否线程安全，运行在主进程还是异步线程池；
   - 核心设计模式或主要调用方。
2. **函数与类级 Docstring**：所有公开函数、类及复杂私有方法必须包含：
   - 一句话描述功能；
   - `Args:` 参数名称、类型与业务含义；
   - `Returns:` 返回值结构与业务含义；
   - `Raises:` 可能显式抛出的领域异常（如 `SiteIssueCode`、`DownloadError`）。
3. **“为什么做 (Why)”大于“做了什么 (What)”**：
   - 对于反爬签名、Range 协议协商、ISAAC64 头部流解密、Win32 COM STA 反射、Job Object 级联销毁等复杂边界分支，必须用行内注释详细说明**平台特征原因与安全防御意图**，严禁只写废话注释。

---

## 4. 测试驱动与回归门禁 (Testing Gate)

智能体在完成任何代码修改后，**必须在当前会话内主动执行测试套件验证，严禁要求用户手动验证**：

1. **单测必跑**：凡修改了具体模块，必须第一时间运行对应的测试文件（如 `pytest tests/test_platforms.py`）。
2. **全量回归保障**：在宣布任务完成或准备提交前，必须运行全量测试套件（`pytest -q`），确保除已记录的历史已知 WIP 外，**1900+ 项测试用例全部保持绿色 (100% Passing)**。
3. **零新增报警**：改动不得引入任何新的 `ImportError`、未处理的 `SyntaxWarning` 或破坏既有断言。

---

## 5. 日常工程记录模板遵循 (Record Templates)

智能体在执行日常缺陷修复、特性开发或进度交接时，必须采用 `docs/templates/` 中预设的标准模板进行落盘归档：
- **缺陷排查**：按照 `BUG_TRACKING_TEMPLATE.md` 在 `docs/records/bugs/` 建档；
- **修复复盘**：按照 `BUGFIX_RECORD_TEMPLATE.md` 在 `docs/records/fixes/` 建档；
- **新功能/新平台**：按照 `FEATURE_RECORD_TEMPLATE.md` 在 `docs/records/features/` 建档；
- **每日/轮次交接**：按照 `DAILY_PROGRESS_TEMPLATE.md` 在 `docs/records/progress/` 建档。
