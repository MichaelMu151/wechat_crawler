# 微信公众号存档爬虫 · 从零开始使用手册

本手册假设：你面前是一个**空文件夹**，电脑上还没有任何项目文件。  
按顺序做完后，你会得到：

```text
（你新建的空文件夹）/
├── wechat_crawler/           ← 本爬虫（GitHub）
├── schinza-wechat-certificate-main/ ← 微信桌面端凭证采集工具
└── name_list.xlsx            ← 你要爬的公众号名单（自己做）
```

这两个 GitHub 仓库是**分开的**。Schinza 负责在你已登录的微信桌面端会话中采集短期凭证，crawler 负责限速拉取、SQLite 入库、正文与导出。

---

## 你需要提前准备

1. 已安装 **Git**、**Python 3.11+**（终端能运行 `git --version`、`python3 --version`）  
2. 已登录的微信桌面客户端，并且只处理你有权存档的公众号内容
3. 大约 20 分钟  

---

## 第 1 步：建一个空工作目录

在终端执行（路径可改成你喜欢的位置）：

```bash
mkdir -p "$HOME/Desktop/wechat-work"
cd "$HOME/Desktop/wechat-work"
pwd
```

确认终端提示符所在目录就是这个空文件夹。

---

## 第 2 步：下载两个 GitHub 仓库

仍然在 `wechat-work` 里执行：

```bash
# 仓库 A：爬虫（本项目）
git clone https://github.com/MichaelMu151/wechat_crawler.git
cd wechat_crawler
git switch cursor/wechat-archive-enhancements
cd ..

# 仓库 B：Schinza 桌面凭证工具（本项目不包含其 GUI/MITM 代码）
git clone https://github.com/Alexxxxxxxxxxxxy/schinza-wechat-certificate.git schinza-wechat-certificate-main
```

做完后检查：

```bash
ls
```

应看到两个文件夹：

```text
wechat_crawler
schinza-wechat-certificate-main
```

若 `git switch` 报错，可改用：

```bash
cd wechat_crawler
git checkout cursor/wechat-archive-enhancements
cd ..
```

由于代码会不时更新，所以如果不想从新创建一个文件夹的话，可以在如上创建的文件夹里（以wechat_work为例）：

```bash
# 1. 更新爬虫项目 (wechat_crawler)
cd wechat_crawler
git pull
cd ..

# 2. 更新 Schinza
cd schinza-wechat-certificate-main
git pull
cd ..
```

---

## 第 3 步：准备公众号名单 `name_list.xlsx`

在 `wechat-work` 目录下新建 Excel，保存为：

```text
$HOME/Desktop/wechat-work/name_list.xlsx
```

表头必须是两列：

| nickname | link |
|----------|------|
| 云南省第一人民医院 | https://mp.weixin.qq.com/s/xxxxx |

说明：

- `nickname`：微信里显示的公众号名称，**必须完全一致**  
- `link`：该号任意一篇公开文章链接（短链也可以）；没有链接时也能试，但有链接更稳  

也可以先生成示例表（在爬虫目录里）：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
python3 scripts/create_name_list_example.py --out ../name_list.xlsx
```

然后用 Excel 打开 `../name_list.xlsx`，改成你的真实名单。

---

## 第 4 步：安装爬虫（每次更新之后建议在安装一次）

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt
python run.py init-db
```

以后每次使用爬虫，先执行：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
source .venv/bin/activate
```

看到提示符前有 `(.venv)` 即表示环境已激活。

---

## 第 5 步：用 Schinza 刷新目标公众号凭证（拉历史前必做）

### 5.1 启动 Schinza

macOS / Windows 新手建议直接从
[Schinza Releases](https://github.com/Alexxxxxxxxxxxxy/schinza-wechat-certificate/releases)
下载成品：

- macOS Apple Silicon：下载 DMG，将 `Schinza.app` 拖到“应用程序”；第一次启动请右键
  `Schinza.app` →“打开”。
- Windows x64：下载 ZIP，完整解压整个 `Schinza` 文件夹后运行 `Schinza.exe`；
  不要只复制 exe，也不要删除 `_internal`。
- 如果当前工作目录里已有源码，也可在其目录运行 `python main.py`；具体依赖见 Schinza
  自己的 README。

### 5.2 首次安装 CA

1. 登录微信桌面客户端。
2. 打开 Schinza 的 **Credential Manager（凭证管理）**。
3. 点击 **Install CA（安装 CA）**。macOS 可能要求输入电脑登录密码。
4. 完全退出并重新启动微信桌面客户端，使本地代理和证书生效。

Schinza 抓取凭证时会临时使用本机 `127.0.0.1:8088` 代理。正常退出 Schinza 会恢复
系统代理；如果退出后网络异常，请先关闭系统中的手动代理，再重启 Schinza/微信。

### 5.3 添加并捕获公众号

1. 在 Schinza 中填写公众号名称和该号任意一篇文章 URL。
2. 名称必须与 `name_list.xlsx` 的 `nickname` **逐字一致**。
3. 点击 **Add & Capture（添加并捕获）**。
4. 回到微信桌面端，重新打开该公众号文章；只刷新旧页面不一定能触发完整凭证。
5. Schinza 中该账号显示 active、出现约 30 分钟倒计时后再继续。

批量公众号可使用 Schinza 的 Batch Import，但建议第一次先只添加一个公众号，完成
端到端测试后再批量操作。

凭证通常只有约 **30 分钟**有效。Schinza 会把凭证写入：

```text
$HOME/Desktop/wechat-work/schinza-wechat-certificate-main/data/accounts.json
```

该文件包含敏感短期密钥，不要提交 Git、上传网盘或发给他人。

> 安全提示：CA 只用于你自己的本机和已授权微信会话。不要分发 CA 私钥，也不要把
> `accounts.json` 中的 `uin`、`key`、`pass_ticket` 发给任何人。

---

## 第 6 步：在 Schinza 一键拉取列表并归档正文

在 Schinza 的“历史文章”中选择已有凭证的公众号（默认全部历史），点击 **拉取并归档正文**：

- 不需要勾选或浏览文章卡片，界面只显示篇数、页数、用时和正文进度；
- 列表会自动翻页，拉完后自动开始正文归档；
- 历史分页写入 `data/history_cache.sqlite`，正文写入 `data/archives/公众号名/`；
- 停止或崩溃后，对同一账号再点一次即可续跑，已完成正文会跳过；
- 出现微信频控时会自动暂停，不会继续硬跑。

归档目录包含 `manifest.json`、流式 `manifest.jsonl`、`articles/*.md`、
`archive_index.sqlite`、`job_state.jsonl`、`failures.jsonl` 和
`summary.json`。超过 1 万篇时，目录清单以 JSONL 存储，避免巨大嵌套 JSON；
其中不包含微信短期密钥。

---

## 第 7 步：离线合并到原 SQLite

先导入账号名单：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
source .venv/bin/activate
python run.py import-list
```

第一次先执行 dry-run，只报告而不写库：

```bash
python run.py import-schinza-export \
  --manifest "../schinza-wechat-certificate-main/data/archives/公众号名称/manifest.json" \
  --articles-dir "../schinza-wechat-certificate-main/data/archives/公众号名称/articles" \
  --dry-run
```

确认 `matched_account=true`、数量合理后，去掉 `--dry-run` 正式合并：

```bash
python run.py import-schinza-export \
  --manifest "../schinza-wechat-certificate-main/data/archives/公众号名称/manifest.json" \
  --articles-dir "../schinza-wechat-certificate-main/data/archives/公众号名称/articles"
python run.py status
```

该命令完全离线，不访问微信。它按规范化文章 URL 去重；已有正文不会覆盖，
只有新文章或旧库缺失的正文会补入。旧版 Schinza 导出的列表 JSON与散落的 Markdown
也支持导入：将 `--manifest` 指向列表 JSON、`--articles-dir` 指向 Markdown 所在目录即可。

结果位置（均在爬虫目录内）：

| 路径 | 内容 |
|------|------|
| `data/wechat_archive.db` | SQLite 主库 |
| `export/articles.jsonl` | 默认导出 `status=ok` 的文章 |
| `export/account_summary.jsonl` | 按账号篇数、时间跨度、ok/failed/deleted 汇总 |
| `data/logs/{resolve,history,content}_progress.jsonl` | 进度日志（约每 15 秒一行） |

运行 `history` / `content` 时，终端会显示**总进度条 + 当前账号进度**（完成数、速度、ETA、ok/fail）。

---

## 本次能力更新（可持续爬取 / 运维 / 呈现）

面向长跑与百万级档案，仓库已增强以下能力（分支 `cursor/wechat-archive-enhancements`）：

### 1. 历史列表：防频控空转

Schinza 的 `getmsg` 可能返回 `ret=-6 / unknownerror`，旧公众平台后端也可能返回
`ret=200013 / freq control`。这些通常是微信服务端风控，不是“换代理”就能解决。
程序会停止空转、保存断点，并提示等待。

现在默认：

| 行为 | 说明 |
|------|------|
| **跳过 `done`** | `history` 默认只处理 `pending` / `running` / `failed` / `retry_wait` / `need_session` |
| **`--refresh`** | 才把已完成账号纳入增量刷新（按 watermark 停在已见文章） |
| **连续频控熔断** | 连续多个账号触发限流后**停止继续扫号**，并提示建议等待时间 |
| **错误前缀** | `list_error` 以 `rate_limited:` / `auth:` / `api:` / `interrupted:` 开头，便于归类 |
| **限流状态** | 历史限流账号记为 `list_status=retry_wait`（可再跑 `history` 重试） |

```bash
# 日常补拉（推荐）：跳过已完成，遇频控会熔断
python run.py --profile safe history

# 想检查已完成账号有没有新发文
python run.py history --refresh
```

### 2. 正文抓取：有界并发 + 自适应间隔 + 熔断

- 默认 `content_concurrency=2`，间隔与历史列表分开（`content_sleep_*`）
- 成功多时略降速、遇限流抬升间隔；连续限流会**整批熔断暂停**
- **不要**把并发盲目调到 4+；平台与正文配额不同，正文再快也救不了历史 `200013`

### 3. 节奏档位 `--profile` / `config.safe.yaml`

```bash
python run.py --profile safe history     # 历史极慢、熔断严、正文 concurrency=1
python run.py --profile balanced history/content/doctor       # 与当前 config.yaml 目标接近
python run.py --profile fast content     # 仅正文略激进；历史仍保守

# 等价：整份安全配置
python run.py --config config.safe.yaml history
```

| 配置项（`config.yaml` → `crawl`） | 默认 | 说明 |
|----------------------------------|------|------|
| `sleep_min` / `sleep_max` | `10` / `20` | **仅**历史列表 / 解析间隔（秒） |
| `history_page_size` | `15` | 每页推送数（不是文章数）；getmsg 最多按 20 请求 |
| `history_max_pages_per_account` | `100` | 单号单次页数上限；到达后保存断点，下次续跑 |
| `history_circuit_breaker_threshold` | `3` | 连续账号频控后熔断 |
| `history_global_cooldown` | `3600` | 熔断后建议等待秒数（进程内也会冷却） |
| `history_rate_limit_cooldown` | `900` | 单次频控后的账号级冷却 |
| `content_concurrency` | `2` | 正文并发 |
| `content_sleep_min` / `content_sleep_max` | `1.5` / `3.5` | 正文间隔 |
| `content_circuit_breaker_threshold` | `5` | 正文连续限流后暂停 |
| `content_rate_limit_cooldown` | `600` | 正文限流冷却 |

### 4. 运维命令（不用写 SQL）

```bash
python run.py doctor              # 凭证剩余、错误 Top、卡住 running、建议下一步
python run.py errors              # list / resolve / content 错误聚合
python run.py status              # 漏斗 + 错误分类 + 建议动作
python run.py status --json       # 便于周报 / 脚本
python run.py retry-failed        # 把正文 failed 重置为 listed，再跑 content
python run.py export-account-summary
```

### 5. Web 管理台（Legacy 可选）

```bash
cd web_ui && npm install && npm run build && cd ..
python run.py serve
# 浏览器打开 http://127.0.0.1:8000
```

看板仍可查看统计和运行旧任务，但本次 Schinza 首次使用流程以 CLI 为准。
请先在终端完成 `import-schinza-credentials`。

### 6. 重要注意事项（请先读）

1. **配额比速度更重要**：历史接口保持单线程，不要自行并发扫号。
2. **看到 `unknownerror` / `rate_limited` / `freq control`：先停跑**，等数小时到一天，再刷新 Schinza 凭证并用 `--profile safe history`。继续硬跑只会加重风控。
3. **`import-list` 全是 skipped**：名单已在库里，属正常；用 `status` / `doctor` 看下一步。  
4. **Schinza 路径不需要 `resolve`**：`import-schinza-credentials` 会按精确名称完成映射。
5. **Schinza 凭证约 30 分钟过期**：`need_session` 或 `auth:` 错误 → 在 Schinza 刷新该号，再次导入。
6. **更新代码后**：`git pull`，并建议重新 `pip install -r requirements.txt`；若用 Web，需在 `web_ui` 里 `npm run build`。
7. **仅学术存档用途**：控制频率；不抓阅读量/点赞。

### 推荐长跑流程

```bash
python run.py doctor
# 若提示大量限流 → 等待后再继续

python run.py --profile safe history     # 补历史列表（跳过 done）
python run.py content                    # 抓正文
python run.py retry-failed && python run.py content   # 可选：重试正文失败
python run.py status
python run.py export-jsonl
python run.py export-account-summary
```

---

## 如何理解 `python run.py status` 的两张表

执行 `status` 后通常会看到类似：

```text
          账号状态
┏━━━━━━━━━┳━━━━━━━━━┳━━━━━━━┓
┃ resolve ┃ list    ┃ count ┃
┡━━━━━━━━━╇━━━━━━━━━╇━━━━━━━┩
│ ok      │ done    │     1 │
│ ok      │ pending │     1 │
│ pending │ pending │     9 │
└─────────┴─────────┴───────┘
        文章状态
┏━━━━━━━━┳━━━━━━━┓
┃ status ┃ count ┃
┡━━━━━━━━╇━━━━━━━┩
│ listed │  1299 │
│ ok     │    10 │
└────────┴───────┘
```

这不是报错，而是**进度看板**。上面管「公众号」，下面管「文章」。

### 上面：账号状态（每个公众号一行汇总）

每个公众号有两个阶段：

| 列 | 含义 |
|----|------|
| `resolve` | 账号是否已映射；Schinza 导入成功后会自动变为 `ok` |
| `list` | 有没有拉完该号的**历史文章列表**（只有标题/链接/时间等元数据） |
| `count` | 处于这种组合状态的公众号数量 |

对上表示例的读法：

| 组合 | 人数 | 意思 |
|------|------|------|
| `resolve=ok` + `list=done` | 1 | 已映射 Schinza 账号，且历史列表已拉完 |
| `resolve=ok` + `list=pending` | 1 | 已映射，但历史列表还没拉（或还没轮到） |
| `resolve=pending` + `list=pending` | 9 | 尚未匹配 Schinza 账号；检查名称并重新导入 |

常见 `resolve` 取值：

- `pending`：尚未匹配 Schinza 账号
- `ok`：已取得明确的账号映射，可以拉历史
- `failed`：旧公众平台解析失败；仅 Legacy 后端需要处理

常见 `list` 取值：

- `pending`：未拉历史列表  
- `running`：正在拉（异常中断后，下次 `history` 会回收为可重试）  
- `done`：该号历史列表已完成（默认不再扫；要增量用 `--refresh`）  
- `failed`：拉列表失败（业务/接口错误等）  
- `retry_wait`：多为历史**限流**后的可重试状态（再跑 `history`）  
- `need_session`：Schinza 短期凭证失效；刷新该号后重新导入

`status` / `doctor` 还会给出**建议下一步**；也可用：

```bash
python run.py doctor
python run.py errors
```

对应下一步：

```bash
# resolve=pending → 检查 Schinza 名称与名单完全一致，然后重新映射
python run.py import-schinza-credentials

# 已有 resolve=ok 但 list 仍是 pending / failed / retry_wait → 继续拉历史
python run.py --profile safe history

# list=need_session → 在 Schinza 刷新该号，再导入并续跑
python run.py import-schinza-credentials
python run.py --profile safe history
```

### 下面：文章状态（每篇文章一条）

历史列表拉下来后，先只有「目录信息」；正文要另一步 `content` 去抓。

| status | 含义 | 下一步 |
|--------|------|--------|
| `listed` | 已在列表里（有标题/链接等），**正文还没抓**或未成功 | `python run.py content` |
| `ok` | 正文已抓成功（HTML + 纯文本都有） | 可分析 / `export-jsonl` |
| `failed` | 正文抓取失败（重试耗尽） | `python run.py retry-failed` 后再 `content` |
| `deleted` | 原文已失效/删除 | 一般无需处理，仅作记录 |
| `out_of_range` | 发布时间不在 `config.yaml` 的时间窗内 | 若需要可改时间窗后重跑 |
| `retry_wait` | 临时失败/限流，等待冷却后重试 | 再跑 `content` |

对上表示例的读法：

- `listed = 1299`：已经知道有 1299 篇文章的元数据，但正文还在排队  
- `ok = 10`：其中 10 篇正文已抓完（例如你用过 `--limit 10`）

所以整体进度可以理解为：

```text
11 个公众号里：
  1 个：Schinza 映射成功 + 列表已拉完
  1 个：Schinza 映射成功 + 列表还没拉
  9 个：尚未匹配 Schinza 账号

文章：
  1309 篇已进入数据库（1299 + 10）
  其中 10 篇正文完成，1299 篇还差正文
```

### 推荐操作顺序（对照状态）

```text
import-list  →  账号出现在库里（已存在则 skipped）
import-schinza-credentials → 按名称映射，resolve 变为 ok
history      →  list 变为 done，listed 文章变多（默认跳过已 done）
content      →  listed 逐步变成 ok
doctor/status→  看错误分类与建议动作
export-*     →  导出文章明细与按账号汇总
```

若你只想先看正文是否正常，可以：

```bash
python run.py content --limit 10
python run.py status
```

看到 `ok` 增加、`listed` 减少（或减少幅度等于本次成功数），就说明正文链路正常；然后再去掉 `--limit` 慢慢跑完。

---

## 第 8 步：用 Python 分析（论文最小路径）

```bash
# 先导出（可选）
python run.py export-jsonl
python run.py export-account-summary
```

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("data/wechat_archive.db")

# 文章正文（默认分析对象）
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

# 按账号汇总（也可直接读 export/account_summary.jsonl）
summary = pd.read_sql_query(
    """
    SELECT
      a.account_name,
      COUNT(ar.id) AS articles_total,
      SUM(CASE WHEN ar.status='ok' THEN 1 ELSE 0 END) AS articles_ok,
      MIN(ar.publish_time) AS first_publish,
      MAX(ar.publish_time) AS last_publish
    FROM accounts a
    LEFT JOIN articles ar ON ar.account_id = a.id
    GROUP BY a.id
    ORDER BY articles_ok DESC
    """,
    conn,
)
print(summary.head())
```

也可用本地 API 全文检索（需先 `python run.py serve`，或直接查库里的 FTS）。

---

## 一键对照：正确目录长这样

```text
wechat-work/
├── name_list.xlsx
├── wechat_crawler/              # clone 自 MichaelMu151/wechat_crawler
│   ├── .venv/
│   ├── config.yaml              # 默认指向名单与 Schinza accounts.json
│   ├── config.safe.yaml         # 频控后的安全档配置
│   ├── run.py
│   ├── data/wechat_archive.db
│   └── export/
└── schinza-wechat-certificate-main/
    ├── main.py
    └── data/accounts.json       # 运行并捕获凭证后生成，勿提交/外传
```

---

## 常见错误

| 报错 / 现象 | 原因 | 处理 |
|-------------|------|------|
| `accounts.json` 找不到 | Schinza 尚未运行或路径不对 | 启动同级 Schinza，并检查 `platform.schinza_accounts_path` |
| `unmatched` | 两边公众号名称不完全一致 | 修正 Schinza 名称或 `name_list.xlsx` 后重新 import |
| `need_session` / `auth:` | Schinza 短期凭证过期/缺失 | 在 Schinza 刷新该号，再执行 `import-schinza-credentials` |
| `unknownerror` / `ret=-6` / `rate_limited:` | 微信服务端风控 | **停跑**，等待数小时到一天，刷新凭证后用 safe 档续跑 |
| `history` 进度 `ok=0`、fail 猛涨、入库 0 | 连续频控空转（旧行为）或仍在硬跑 | 升级到本分支后应会熔断；仍见此象请 Ctrl+C 并换 `safe` |
| `import-list` → `inserted:0, skipped:N` | 名单已导入过 | 正常；看 `status` / `doctor` |
| 名单读不到 | `name_list.xlsx` 位置不对 | 放在 `wechat-work/name_list.xlsx`（与两个仓库同级） |
| Schinza 捕获不到凭证 | CA/代理未对重启后的微信生效，或只刷新了旧页面 | 重启微信，重新打开文章或滚动公众号历史页；查看 Schinza 的 `data/capture_debug.log` |
| 退出 Schinza 后网络异常 | 系统代理未恢复 | 关闭系统手动代理，确认不再指向 `127.0.0.1:8088`，再重启应用 |

---

## 可选：一键初始化脚本

若你已经完成第 2 步（两个仓库都在同级），可在爬虫目录执行：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
bash scripts/setup_from_scratch.sh
```

它会：创建虚拟环境、安装依赖、初始化数据库、生成上级目录的示例名单（若不存在）。  
**不会**替你启动 Schinza、安装其 CA 或操作微信桌面端。

---

## 说明

- 本项目只抓标题、作者、时间、正文等学术存档字段，不抓阅读量/点赞。  
- 请控制频率，仅用于学术研究与个人备份；**历史列表接口对频率极敏感**。  
- Schinza 使用 MIT 许可证；本爬虫仅只读其 `accounts.json`，并参考其历史响应解析边界。旧 `wechat-download-api` 后端继续兼容，但不再是默认流程。
- 当前推荐跟踪分支：`cursor/wechat-archive-enhancements`（含熔断、doctor、进度条与安全档配置）。  
- 吞吐自检（可选）：`python scripts/benchmark_scale.py --rows 10000 --content-mock 200`

### Legacy fallback：公众平台后端

如果你所在环境的公众平台登录渠道仍可用，可把 `config.yaml` 的
`platform.backend` 改回 `platform` 或 `download_api`，并继续使用
`import-platform-from-download-api` / `platform-status` / `resolve`。这些命令为旧数据库
保留，但当前默认流程与测试重点是 `schinza_getmsg`。
