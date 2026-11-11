# 研发与质量记录库 (Engineering & Quality Records)

本目录用于存放拾影项目在日常研发、缺陷追踪、功能迭代中的标准化过程记录，严格采用 `docs/templates/` 中定义的规范模板。

## 目录结构规划

```
docs/records/
├── bugs/           # 日常缺陷与漏洞追踪记录 (基于 BUG_TRACKING_TEMPLATE.md)
│   └── BUG-{YYYYMMDD}-{模块}-{序号}.md
├── fixes/          # 修复与技术复盘记录 (基于 BUGFIX_RECORD_TEMPLATE.md)
│   └── FIX-{YYYYMMDD}-{轮次或主题}.md
├── features/       # 新功能与架构变更新增记录 (基于 FEATURE_RECORD_TEMPLATE.md)
│   └── FEAT-{YYYYMMDD}-{功能名称}.md
└── progress/       # 每日推进与测试质量同步日志 (基于 DAILY_PROGRESS_TEMPLATE.md)
    └── PROGRESS-{YYYYMMDD}-R{轮次}.md
```

## 流转指引

1. **发现 Bug 时**：在 `bugs/` 下建立记录，跟踪严重级别与复现步骤；
2. **完成修复后**：在 `fixes/` 下记录具体修改 diff、测试验证情况与经验防范；
3. **开发新特性/新平台时**：在 `features/` 下编写详细功能规格、边界分支设计与验收测试表；
4. **每日交接推进时**：在 `progress/` 下更新当日修复数量、测试状态与次日待办。
