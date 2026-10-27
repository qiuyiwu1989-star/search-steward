# Search Steward

## 目标与档位
G3。为自有站点提供有证据的 SEO/GEO 巡检、整改和效果管理。

## 技术
Python 3.10+、Flask、SQLite、原生 HTML/CSS/JS。先读 TASKS.md、specs/、DECISIONS.md。

## 边界
公开仓库绝不包含生产数据库、私有内容、凭据、会话及完整生产配置。
抓取只限配置授权域和公网目标，保留 DNS 固定、TLS、逐跳校验及请求上限。
提交接受、索引、曝光、AI 引用、合作转化分别记录，未知不得记为零。
新功能先写规格；只暂存本次明确修改文件。不可自动改生产，不新建重复调度。

## 验证
STEWARD_DOMAINS=qiuyiwu.com python3 -m unittest discover -s tests/search_steward -v
node --check assets/search-steward.js

## 落盘
新产物放外置盘；生产状态留在私有数据目录。开源与个人站生产集成分开验收。
