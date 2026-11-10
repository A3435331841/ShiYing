# 平台标识来源

核验日期：2026-11-10。所有资源随应用本地分发，页面运行时不请求外部图床。

| 平台 | 本地文件 | 已核验来源 |
| --- | --- | --- |
| 抖音 | `douyin.png` | [官方 App Store 条目](https://apps.apple.com/cn/app/id1142110895)，发布者 Beijing Douyin Technology Co., Ltd. |
| 抖音精选 | `selected-douyin.png` | [官方 App Store 条目](https://apps.apple.com/cn/app/id6444170965)，发布者 Beijing Douyin Technology Co., Ltd. |
| 快手 | `kuaishou.png` | [官方 App Store 条目](https://apps.apple.com/cn/app/id440948110)，发布者 Beijing Kwai Technology Co., Ltd. |
| 视频号 | `wechat.svg` | [微信视频号助手](https://channels.weixin.qq.com/)使用的 `icon-logo` 原生 SVG；橙色取自同站官方 favicon 的实心像素 `#fa9d3b`。 |
| 小红书 | `xiaohongshu.png` | [官方 App Store 条目](https://apps.apple.com/cn/app/id741292507)，发布者 Xingin Information Technology (Shanghai) Co., Ltd。 |
| B站 | `bilibili.jpg` | [B站官方下载页](https://app.bilibili.com/)的 `img.icon` 蓝色小电视。未采用 App Store 带赛事宣传文字的临时活动图，也未采用带 WEB 角标的站点 touch icon。 |
| YouTube | `youtube.png` | [官方 App Store 条目](https://apps.apple.com/cn/app/id544007664)，发布者 Google LLC。 |

## 资源处理约束

- PNG/JPEG 保留 CDN 原始响应字节，没有重绘、追踪、裁剪、重采样或改色。Apple 资源通过官方 Lookup API 取得地址，向同一 Apple CDN 请求无损 PNG 交付格式。
- 视频号 SVG 从官网加载的资源包中提取为独立文件；只更换外层 symbol 容器并显式设置官方橙色，内部路径、填充规则、viewBox 均不改写。
- `sources.json` 记录原始页面、下载地址、发布者、原始尺寸、处理方式及 SHA-256；视频号额外记录原始资源包与路径哈希。
- 品牌图标使用独立 `<img>`，等比 `object-fit: contain` 显示，不继承界面 SVG 的描边，不应用透明度衰减或主题滤镜。图标背景仅随完整应用图标保留，不额外叠加底色。
- 抖音精选以本次实际下载的青绿底黑白音符原图为准，不套用旧版“精选”角标说明，不添加自创星标。

以上商标及图形的权利归各自权利人所有，仅用于标识支持的平台，不代表官方合作、授权或背书，也不纳入本项目软件许可证的授权范围。
