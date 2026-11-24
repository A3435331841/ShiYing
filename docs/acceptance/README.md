# 平台验收证据目录

本目录存放真实平台下载验收的可复核证据。`data/capabilities.json` 中的
`verified_at` / `evidence` 只能引用本目录内的相对路径。

## 命名

`<平台>-<YYYYMMDD>-<作品标识>.md`，例如 `kuaishou-20261123-3xAbC9.md`。

## 必填字段（见 `模板.md`）

平台、日期、作品链接、条目数、实际分辨率、文件 SHA-256、执行人。

## 规则

1. 未实际完成下载不得写入证据文件，能力状态保持 `experimental` 或 `unavailable`。
2. 证据文件缺失时能力状态不自动升降，仅在接口标记 `evidence_missing=true`；填写了 `verified_at` 却没填 `evidence` 路径，同样按证据缺失标记（不能当作「无需证据」）。
3. 不得在证据文件中记录 Cookie、令牌、临时直链或绝对路径。