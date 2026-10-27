# Search Steward · SEO / GEO 管家

让网站的**发现 → 巡检 → 整改 → 复验 → 提交回执 → 引用观测 → 效果记录**有据可查。

从邱懿武个人网站后台的在用模块独立提取，保留 SQLite 存储、Flask API、中文后台及有边界的公开巡检。公开的是代码，不是管理后台、凭据或业务数据库。

## 已有能力

- 多站点登记；配置允许管理的域名。
- robots、sitemap、HTML 巡检；DNS/IP 校验、防私网请求、重定向逐跳验证、响应大小限制。
- 断点续查、问题复现重开、整改状态及复验证据。
- 搜索引擎提交回执的幂等导入；未知结果与失败分开。
- 平台接入证据、GEO 真实回答/引用/人工审校、搜索效果历史。
- 独立运行或通过 Blueprint 接入已有 Flask 后台。

**不包含**：排名保证、模型引用保证、通用内容自动生成、搜索平台 OAuth 连接、独立调度器。`receipts.py` 是旁路记录器；002 新增执行器默认 dry-run，只有显式执行才发送。生产系统原有定时任务不随仓库复制。

## 本地启动

Python 3.10+。源码和内容清单应放在你自己的项目目录，数据库放在源码目录之外。

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp examples/sites.json sites.json
cp examples/content.json content.json
# 编辑两个清单，换成自己的公开站点和内容。
export STEWARD_DOMAINS=example.com
export STEWARD_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
# 将令牌安全保存在密码管理器，打开后台时输入；不要写入仓库。
export QIU_SEARCH_STEWARD_DB="$HOME/.local/share/search-steward/state.sqlite3"
python3 app.py
```

打开 <http://127.0.0.1:8765>。界面输入同一令牌；只保存在标签会话存储。启动入口默认仅监听本机，不是生产 WSGI 服务。不要直接暴露到公网；远程部署应配置 HTTPS、生产 WSGI 服务和访问控制。Bearer 认证保护全部数据 API；HTML/CSS/JS 仅为无业务数据的界面。

`STEWARD_DOMAINS` 为逗号分隔的授权根域名，包含其子域；默认 example.com，进程启动时读取。`STEWARD_CONTENT_ROOT` 指定存放 `sites.json` 和 `content.json` 的目录，默认源码根目录。`QIU_SEARCH_STEWARD_DB` 必须在源码目录外。

CLI 与网页必须使用同一域名配置、内容目录和数据库：

```sh
STEWARD_DOMAINS=example.com python3 scripts/search_steward.py scan --site https://example.com/ --limit 3
```

CLI 内容目录同样使用 `STEWARD_CONTENT_ROOT`。默认单次 50 页，支持 1—200 页，约四分钟预算；不是无限全站爬虫。

## 内容覆盖

通用模式读取显式 `content.json`，仅展示原始地址的实际探测记录。尚未计算的地图/目录覆盖为未知，不冒充缺失或合格。`pipeline/search_steward/qiuyiwu_coverage.py` 是保留的个人站目录适配器，不会默认调用；它依赖宿主站的公开清单文件，不包含那些数据。

## 验证

存量测试使用公开网址作为离线模拟夹具，不会真实提交或抓取站点：

```sh
STEWARD_DOMAINS=qiuyiwu.com python3 -m unittest discover -s tests/search_steward -v
node --check assets/search-steward.js
```

34 项测试覆盖持久化、权限、SSRF、续查、复验、回执和独立入口。CI 不读取生产数据、密钥，也不触发部署。

## 后续工作

- [现状、系统边界和接手发现](docs/ASSESSMENT.md)
- [提升任务与验收标准](docs/ROADMAP.md)
- [当前状态](TASKS.md)
- [开源提取规格](specs/001-standalone.md)

本仓库独立运行通过测试，尚未替换个人站的生产模块。采用 MIT License。

## 公开清单与发布闸门（002）

清单使用 `schema_version: 1`。内容必须有稳定 `id`、`kind`、`title`、`version`、`status: published` 和 `public: true`；发布闸门还要求相对的 HTML `path`。例子可直接检验：

```sh
STEWARD_DOMAINS=example.com python3 scripts/steward_ops.py validate --root examples
STEWARD_DOMAINS=example.com python3 scripts/steward_ops.py gate --root examples
```

闸门拒绝空清单、路径越界/私有路径、无可读正文、缺少标题、错误 canonical、noindex 冲突及坏 JSON-LD。描述缺失、短文本、缺少 H1 是提醒，不假装为排名预测。它不代替线上检查。

## 显式提交执行器（002）

**不会安装定时任务。** 生产先执行[切换预案](docs/MIGRATION-002.md)，统一已有执行器及历史状态，不能把新队列当成空白历史重新提交。

```sh
# 只将已审核清单的正式、可索引地址入队；显式版本构成去重键。
python3 scripts/steward_ops.py enqueue --root /private/content --queue-db /private/state/queue.sqlite3
# 默认 dry-run，不发送请求。
python3 scripts/steward_ops.py submit --host example.com --queue-db /private/state/queue.sqlite3
# 配置此域名的 STEWARD_BAIDU_TOKEN 后，由操作者显式加 --execute。
```

每次最多十条，单 URL 回执确认；实际额度以服务端为准。请求不明确进入 `unknown`，阻止同域继续发送；进程崩溃留下 `sending`，同样必须凭依据人工恢复。状态不会因超时被当作失败自动重试。明确拒绝保留为 `failed`；额度不足保留 `pending`。源码不记录密钥或原始服务端报文。

`export-receipts --store-db ... --site-id ...` 将回执幂等导入管家。队列和管理库必须保存在私有位置，使用同一个 runner 标识重复导入。当前不自动导入历史队列，也不自动补报人工核验结果；见迁移预案。

问题聚合入口为后台“问题聚合”栏目和 `/api/inside/search-steward/issues/groups`：按站点/规则聚合未关闭任务，保留原始状态和证据，不能据此认定同一根因。
