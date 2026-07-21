# 微信公众号学术存档与管理系统

本项目用于硕士论文、学术研究和个人内容备份。给定公众号名单后，可抓取 **2018 至今** 的历史推文标题、作者、时间、HTML 与纯文本，写入本地 **SQLite**，并提供命令行、HTTP API 与 Web 管理台。

## 2026.7 关键：适配微信改版（公众平台凭证）

微信改版后，个人客户端 `mp/profile_ext?action=getmsg` 抓包路径已不可靠，本项目默认改为：

1. 使用**微信公众平台后台**登录凭证（任意订阅号/服务号管理员扫码即可）
2. 调用 `searchbiz` 按昵称解析 `fakeid`
3. 调用 `appmsgpublish` 分页拉取任意公开公众号的历史发文列表
4. 正文仍直接请求文章 URL 解析 HTML / 纯文本

实现参考社区项目 [wechat-download-api](https://github.com/tmwgsicp/wechat-download-api) 的公开接口用法；本仓库为独立实现，保留原有 SQLite 存档、任务队列、断点续跑、限速与导出能力。

> **你需要准备：** 一个自己的微信公众号（订阅号即可），以及该号管理员微信，用于扫码登录公众平台。登录后可查询**任意**公开公众号的文章列表（不限于自己的号）。凭证约 **4 天**有效，过期后重新扫码导入即可。

---

## 1. 能做什么 / 不做什么

### 可以

- 从 Excel 导入「公众号昵称 + 样例文章链接」
- 用公众平台凭证搜索并解析 `fakeid`
- 分页拉取历史文章列表（可断点续跑）
- 抓取正文 HTML + 纯文本（图片只保留链接）
- 标记失效文、超出时间窗、失败重试
- CLI / API / Web 管理台
- 导出 JSONL，供 Python/pandas 分析

### 不做

- 不再依赖个人微信 `getmsg` 抓包（旧命令仅兼容保留）
- 不抓阅读量 / 点赞 / 评论
- 不保证任意账号 100% 历史完整（受平台限流与账号状态影响）
- 不破解验证码或绕过风控

---

## 2. 安装

```bash
git clone https://github.com/MichaelMu151/wechat_crawler.git
cd wechat_crawler
git switch cursor/wechat-archive-enhancements   # 或你的主分支

python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python run.py init-db
```

名单默认读取 `../name_list.xlsx`（可在 `config.yaml` 修改），表头：

| nickname | link |
|----------|------|
| 云南省第一人民医院 | https://mp.weixin.qq.com/s/xxxxx |

`link` 可选；有公众平台凭证时优先按昵称搜索。有链接时解析失败可回退。

---

## 3. 配置公众平台凭证（必做，才能拉历史）

任选一种方式。

### 方式 A（推荐）：借助 wechat-download-api 扫码登录

该工具提供扫码登录页，凭证可保存约 4 天。

```bash
# 1) 启动 wechat-download-api（你本地已有源码时可直接用）
cd "../wechat-download-api-main"
cp env.example .env
# 编辑 .env：SITE_URL=http://127.0.0.1:5000
bash start.sh
# 或: docker compose up -d

# 2) 浏览器打开 http://127.0.0.1:5000/login.html
#    用「公众号管理员微信」扫码登录

# 3) 回到本项目，导入凭证
cd "../wechat_archive"   # 或 wechat_crawler 仓库根目录
source .venv/bin/activate
python run.py import-platform-from-download-api \
  --env ../wechat-download-api-main/.env

python run.py platform-status
```

若 Docker 把凭证写在 `data/.credentials.json`，同样指向其 `.env` 所在目录即可（导入逻辑会同时查找该 JSON）。

也可把本项目 `config.yaml` 设为直接调用其 HTTP API：

```yaml
platform:
  backend: download_api
  download_api_base_url: http://127.0.0.1:5000
```

此时无需导入 `.env`，只要 download-api 保持登录即可。

### 方式 B：浏览器手动复制 token / Cookie

1. 打开 [微信公众平台](https://mp.weixin.qq.com/) 并扫码登录  
2. 登录后看地址栏或接口请求中的 `token=...`  
3. DevTools → Network → 任意 `mp.weixin.qq.com` 请求 → 复制 Request Headers 的整段 `Cookie`  
4. 执行：

```bash
python run.py set-platform-creds
# 按提示粘贴 token 与 cookie
python run.py platform-status
```

凭证保存在 `sessions/platform_credentials.yaml`（已 gitignore，勿提交）。

---

## 4. 日常采集流程

```bash
# 导入名单
python run.py import-list

# 解析 fakeid（有平台凭证时走 searchbiz）
python run.py resolve

# 拉历史列表（2018–2026，见 config.yaml）
python run.py history
# 可先小规模试跑：
python run.py history --limit 1

# 抓正文
python run.py content

# 查看进度
python run.py status

# 导出分析用 JSONL
python run.py export-jsonl
```

一键试点（无凭证时只抓样例正文）：

```bash
python run.py pilot --limit 2
```

### Web 管理台

```bash
cd web_ui && npm install && npm run build && cd ..
python run.py serve
# 打开 http://127.0.0.1:8000
```

---

## 5. 配置要点（`config.yaml`）

```yaml
paths:
  name_list: ../name_list.xlsx
  database: data/wechat_archive.db
  platform_credentials: sessions/platform_credentials.yaml

platform:
  backend: platform          # 或 download_api
  download_api_base_url: http://127.0.0.1:5000

crawl:
  start_date: "2018-01-01"
  end_date: "2026-12-31"
  sleep_min: 4
  sleep_max: 9
  history_page_size: 20      # appmsgpublish 最大 100；建议 10–20
```

慢速优先：不要把间隔压得太低，也不要提高 `jobs.workers` 去并发刷历史列表。

---

## 6. 用 Python 分析

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("data/wechat_archive.db")
df = pd.read_sql_query(
    """
    SELECT a.account_name, ar.title, ar.publish_time, ar.content_text, ar.url
    FROM articles ar
    JOIN accounts a ON a.id = ar.account_id
    WHERE ar.status = 'ok'
    """,
    conn,
)
print(df.head())
```

---

## 7. 常用命令

| 命令 | 作用 |
|------|------|
| `python run.py platform-status` | 检查公众平台凭证 |
| `python run.py import-platform-from-download-api --env ...` | 从 download-api 导入凭证 |
| `python run.py set-platform-creds` | 手动写入 token/cookie |
| `python run.py import-list` | 导入 Excel 名单 |
| `python run.py resolve` | 解析 fakeid |
| `python run.py history` | 拉历史列表 |
| `python run.py content` | 抓正文 |
| `python run.py status` | 进度 |
| `python run.py export-jsonl` | 导出 |
| `python run.py backup-db` | 一致性备份 |
| `python run.py maintain-db` | 库维护 |
| `python run.py serve` | API + 管理台 |

文章状态：`listed` → `ok` / `deleted` / `failed` / `out_of_range` / `retry_wait`  
账号列表状态：`pending` / `running` / `done` / `failed` / `need_session`（凭证过期）

---

## 8. 故障排除

| 现象 | 处理 |
|------|------|
| `未配置公众平台凭证` | 先 `import-platform-from-download-api` 或 `set-platform-creds` |
| `need_session` / 登录失效 | 重新扫码登录 download-api 并再次导入；凭证约 4 天 |
| `频繁` / 限流 | 增大 `sleep_min/max`，等待冷却后继续；支持断点续跑 |
| 搜索不到公众号 | 核对昵称是否与微信完全一致；可补样例链接作回退 |
| 正文失败 | `python run.py retry-failed` 后重跑 `content` |

旧版 `import-session-har`（个人微信 getmsg）已弃用，仅作兼容保留。

---

## 9. 大批量续航说明（摘要）

面向十万至百万篇单机归档：有界批次读取、可中断续跑、会话/限流分别处理、FTS5 与 keyset 分页、`backup-db` / `maintain-db`。默认 `jobs.workers: 1`。百万篇全文请预留足量 SSD（HTML 体积很大）。详见仓库历史提交说明与 `scripts/benchmark_scale.py`。

---

## 10. 合规与安全

- 仅供学术存档与个人备份，控制频率，遵守平台规则与研究伦理  
- 不要提交 `sessions/platform_credentials.yaml`、数据库、导出文件  
- 不要在论文附录、截图、Issue 中公开 token / Cookie  
- 参考项目 wechat-download-api 为 AGPL-3.0；若你直接部署并修改其服务端代码，请遵守其许可证。本爬虫通过凭证导入或 HTTP 调用与之协作，核心存档逻辑为本仓库独立代码  

---

## 11. Docker（可选）

```bash
docker compose up -d --build
# 将 sessions/platform_credentials.yaml 挂载进容器后再跑 history
```

详见 `compose.yaml` / `Dockerfile`。
