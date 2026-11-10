# Source and license notice

The original rednote_downloader source retains its MIT copyright and license notice in [LICENSE.MIT](LICENSE.MIT).

The Kuaishou integration was adapted from `Tools/DownloaderHelper/vendor/rednote/app/kuaishou.py` and its integration patches in [SeanLi-Coder/ChengYingPlayer](https://github.com/SeanLi-Coder/ChengYingPlayer), source commit `275ac190ec86d781da50eda0ff4be01b0a70dbde`. The original Kuaishou module carries `SPDX-License-Identifier: GPL-3.0-or-later`. Its original attribution remains in the module. The corresponding GPL text is in [LICENSE](LICENSE).

The combined distribution containing this Kuaishou integration is distributed under GPL-3.0-or-later. This does not erase the MIT notice on the original files. Third-party dependencies retain their respective licenses.

## Platform trademarks and artwork

The official platform icons in `app/static/platform-icons/` identify supported services. Their trademarks and artwork remain the property of the respective rights holders and are not licensed under this project's software license. Their inclusion does not imply endorsement, partnership, or affiliation. Source URLs, publishers, processing notes, and content checksums are documented in that directory's `README.md` and `sources.json`.

## Shiying local desktop application

The Shiying desktop shell, Chinese workspace UI and local WeChat Channels adapter are modifications in this distribution. The downloader base was obtained from SeanLi-Coder/rednote_downloader, revision 106966f552e45eb3af75055810ab51f940f767be. The combined application is distributed under GPL-3.0-or-later, while preserving existing MIT attribution.

The Channels share-link flow was referenced from ltaoo/wx_channels_download v261106, internal/workers/sph/worker.js. This distribution implements its own Python adapter and does not include hardcoded third-party account credentials or the upstream playback capture/decryption channel. The availability of that flow has not been verified with a configured user session on this machine.

For Yuanbao response field names (`h264VideoInfo`, `h265VideoInfo`, and `decodeKey`), the adapter was checked against [oliver-zch/wx-video-channel-download](https://github.com/oliver-zch/wx-video-channel-download), which is distributed under MIT. This application implements its own validation and media selection; the compatibility of these fields still requires a real authenticated sample.
