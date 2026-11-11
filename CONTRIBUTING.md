# 贡献指南 (Contributing Guide)

感谢你关注拾影（Shiying）项目！我们欢迎所有有助于提升系统稳定性、画质保真度以及扩展多平台能力的贡献。

在提交 Pull Request 前，请仔细阅读本指南与 [AGENTS.md](AGENTS.md)。

---

## 1. 开发环境搭建

本项目推荐使用 [uv](https://docs.astral.sh/uv/) 进行依赖管理与环境隔离：

```bash
# 1. 克隆代码仓库
git clone https://github.com/A3435331841/ShiYing.git
cd ShiYing

# 2. 基于 uv 创建虚拟环境 (建议 Python 3.10 ~ 3.12)
uv venv .venv --python 3.12

# 3. 安装全量开发与测试依赖
uv pip install -r requirements.lock.txt -r requirements-dev.txt

# 4. 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate
```

---

## 2. 代码风格与规范标准

为保障代码库长期的高内聚与可维护性，所有提交代码必须满足：

1. **类型提示 (Type Annotations)**：
   - 核心函数必须标注完整的 Python 3.10+ 类型注解（`int | None`, `list[str]` 等）。
2. **文档与注释 (Docstring)**：
   - 所有新增或重构的模块、类与方法，必须遵循 [PEP 257](https://peps.python.org/pep-0257/) 标准编写清晰的中文 Docstring，详细注明参数含义与抛出异常。
3. **平台抽象隔离 (SPI)**：
   - 新增平台必须在 `app/platforms/<platform_key>/` 下以独立包形式实现，实现 `PlatformProvider` 声明的 6 大标准接口，严禁侵入核心调度代码。

---

## 3. 本地验证与测试门禁

在提交任何更改前，请在本地完整运行测试矩阵并确保全部通过：

```bash
# 运行特定模块测试
pytest tests/test_platforms.py

# 运行全量单元测试 (确保 1900+ 用例通过)
pytest -q
```

---

## 4. Git 提交规范 (Conventional Commits)

提交信息请遵循以下标准语义前缀：
- `feat:` 新增平台支持或业务功能（如 `feat(platforms): add youtube 4k stream parser`）
- `fix:` 缺陷修复（如 `fix(engine): handle cdn content-length drift in range request`）
- `refactor:` 代码重构（不改变现有行为的结构优化）
- `docs:` 文档更新或规范新增
- `test:` 补充或修改测试用例
- `chore:` 依赖升级、构建配置调整

---

## 5. 提交 Pull Request

1. 从 `main` 分支切出功能分支：`git checkout -b feature/your-feature-name`；
2. 保持每次 commit 的原子性与清晰提交说明；
3. 确保 `.gitignore` 过滤生效，严禁提交任何个人 Cookie、下载数据或缓存；
4. 推送分支并提交 PR，详细描述所解决的问题与本地验证成果。
