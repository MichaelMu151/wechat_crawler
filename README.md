# 微信公众号存档爬虫 · 从零开始使用手册

本手册假设：你面前是一个**空文件夹**，电脑上还没有任何项目文件。  
按顺序做完后，你会得到：

```text
（你新建的空文件夹）/
├── wechat_crawler/           ← 本爬虫（GitHub）
├── wechat-download-api/      ← 扫码登录工具（另一个 GitHub）
└── name_list.xlsx            ← 你要爬的公众号名单（自己做）
```

这两个 GitHub 仓库是**分开的**，必须都下载。爬虫仓库里**不会**自带 `wechat-download-api`。

---

## 你需要提前准备

1. 已安装 **Git**、**Python 3.11+**（终端能运行 `git --version`、`python3 --version`）  
2. 一个你自己的**微信公众号**（订阅号即可）+ 该号的**管理员微信**（用来扫码）  
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

# 仓库 B：扫码登录工具（另一个项目，本爬虫不自带）
git clone https://github.com/tmwgsicp/wechat-download-api.git
```

做完后检查：

```bash
ls
```

应看到两个文件夹：

```text
wechat_crawler
wechat-download-api
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

# 2. 更新扫码登录工具 (wechat-download-api)
cd wechat-download-api
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

## 第 5 步：启动扫码工具并登录（拉历史前必做）

**另开一个终端窗口**，执行：

```bash
cd "$HOME/Desktop/wechat-work/wechat-download-api"

cp env.example .env
```

用文本编辑器打开该目录下的 `.env`，把 `SITE_URL` 改成：

```text
SITE_URL=http://127.0.0.1:5000
```

保存后启动：

```bash
bash start.sh
```

浏览器打开：

http://127.0.0.1:5000/login.html

用**公众号管理员微信**扫码登录。  
登录成功后，这个窗口先不要关。

---

## 第 6 步：把登录凭证导入爬虫

回到爬虫终端（有 `(.venv)`）：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
source .venv/bin/activate

# 默认会读取同级目录 ../wechat-download-api/.env
python run.py import-platform-from-download-api

python run.py platform-status
```

若显示凭证可用，继续下一步。

> 凭证大约 **4 天**过期。过期后：再扫一次码，然后重新执行 `import-platform-from-download-api`。

---

## 第 7 步：开始爬取

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
source .venv/bin/activate

# 1) 导入名单（默认读取上一级目录的 name_list.xlsx）
#    可重复执行：已存在的 nickname+link 会 skipped，不会重复插入
python run.py import-list

# 2) 先小规模试跑
python run.py resolve --limit 2
python run.py --profile safe history --limit 1
python run.py content --limit 10
python run.py status
python run.py doctor

# 3) 确认无误后，去掉 --limit 做全量（历史建议先用 safe）
# python run.py resolve
# python run.py --profile safe history
# python run.py content

# 4) 导出（文章明细 + 按账号汇总）
python run.py export-jsonl
python run.py export-account-summary
```

结果位置（均在爬虫目录内）：

| 路径 | 内容 |
|------|------|
| `data/wechat_archive.db` | SQLite 主库 |
| `export/articles.jsonl` | 默认导出 `status=ok` 的文章 |
| `export/account_summary.jsonl` | 按账号篇数、时间跨度、ok/failed/deleted 汇总 |
| `data/logs/{resolve,history,content}_progress.jsonl` | 进度日志（约每 15 秒一行） |

运行 `resolve` / `history` / `content` 时，终端会显示**总进度条 + 当前账号进度**（完成数、速度、ETA、ok/fail）。

---

## 本次能力更新（可持续爬取 / 运维 / 呈现）

面向长跑与百万级档案，仓库已增强以下能力（分支 `cursor/wechat-archive-enhancements`）：

### 1. 历史列表：防频控空转

公众平台 `appmsgpublish` 极易返回 `ret=200013`（`freq control`）。旧行为会**逐个账号烧冷却**，表现为进度条 `ok=0 fail` 一路涨、入库一直为 0。

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
python run.py --profile balanced …       # 与当前 config.yaml 目标接近
python run.py --profile fast content     # 仅正文略激进；历史仍保守

# 等价：整份安全配置
python run.py --config config.safe.yaml history
```

| 配置项（`config.yaml` → `crawl`） | 默认 | 说明 |
|----------------------------------|------|------|
| `sleep_min` / `sleep_max` | `10` / `20` | **仅**历史列表 / 解析间隔（秒） |
| `history_page_size` | `15` | 每页条数；成功会升、限流会降到 `history_page_size_min` |
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
python run.py retry-resolve       # 把 resolve=failed 重置为 pending，再跑 resolve
python run.py retry-failed        # 把正文 failed 重置为 listed，再跑 content
python run.py export-account-summary
```

### 5. Web 管理台（可选）

```bash
cd web_ui && npm install && npm run build && cd ..
python run.py serve
# 浏览器打开 http://127.0.0.1:8000
```

看板可看统计、错误分类、建议动作，并提交 import / resolve / history / content 任务；支持「历史(含 done 刷新)」「重试解析失败」等按钮。

### 6. 重要注意事项（请先读）

1. **配额比速度更重要**：历史接口不要追求并发；账号级并行会更快触发 `200013`。  
2. **看到大量 `rate_limited` / `freq control`：先 Ctrl+C 停跑**，等 1–2 小时（或更久），再用 `--profile safe history`。继续硬跑只会空转。  
3. **`import-list` 全是 skipped**：名单已在库里，属正常；用 `status` / `doctor` 看下一步。  
4. **`resolve` 返回 total=0**：没有 `pending` 可解析（失败号需先 `retry-resolve`）。  
5. **`resolve=failed` 不会自动重试**：必须 `retry-resolve` 后再 `resolve`。  
6. **凭证约 4 天过期**：`need_session` 或 `auth:` 错误 → 重新扫码并 `import-platform-from-download-api`。  
7. **更新代码后**：`git pull`，并建议重新 `pip install -r requirements.txt`；若用 Web，需在 `web_ui` 里 `npm run build`。  
8. **仅学术存档用途**：控制频率；不抓阅读量/点赞。

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
| `resolve` | 有没有解析出该号的 `fakeid`（平台 ID） |
| `list` | 有没有拉完该号的**历史文章列表**（只有标题/链接/时间等元数据） |
| `count` | 处于这种组合状态的公众号数量 |

对上表示例的读法：

| 组合 | 人数 | 意思 |
|------|------|------|
| `resolve=ok` + `list=done` | 1 | 已解析 ID，且历史列表已拉完 |
| `resolve=ok` + `list=pending` | 1 | 已解析 ID，但历史列表还没拉（或还没轮到） |
| `resolve=pending` + `list=pending` | 9 | 还没解析 ID（通常还没跑 `resolve`，或解析失败未重试） |

常见 `resolve` 取值：

- `pending`：未解析  
- `ok`：已解析成功  
- `failed`：解析失败（**不会**被再次 `resolve` 自动捞起；先 `retry-resolve`）

常见 `list` 取值：

- `pending`：未拉历史列表  
- `running`：正在拉（异常中断后，下次 `history` 会回收为可重试）  
- `done`：该号历史列表已完成（默认不再扫；要增量用 `--refresh`）  
- `failed`：拉列表失败（业务/接口错误等）  
- `retry_wait`：多为历史**限流**后的可重试状态（再跑 `history`）  
- `need_session`：登录凭证失效，需重新扫码并 `import-platform-from-download-api`

`status` / `doctor` 还会给出**建议下一步**；也可用：

```bash
python run.py doctor
python run.py errors
```

对应下一步：

```bash
# 还有 pending 的 resolve → 继续解析
python run.py resolve

# resolve=failed → 先重置再解析
python run.py retry-resolve
python run.py resolve

# 已有 resolve=ok 但 list 仍是 pending / failed / retry_wait → 继续拉历史
python run.py --profile safe history

# list=need_session → 先重新登录再 history
python run.py import-platform-from-download-api
python run.py history
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
  1 个：ID 已解析 + 列表已拉完
  1 个：ID 已解析 + 列表还没拉
  9 个：ID 都还没解析

文章：
  1309 篇已进入数据库（1299 + 10）
  其中 10 篇正文完成，1299 篇还差正文
```

### 推荐操作顺序（对照状态）

```text
import-list  →  账号出现在库里（已存在则 skipped）
resolve      →  resolve 变为 ok
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
│   ├── config.yaml              # 默认指向 ../name_list.xlsx 与 ../wechat-download-api/.env
│   ├── config.safe.yaml         # 频控后的安全档配置
│   ├── run.py
│   ├── data/wechat_archive.db
│   └── export/
└── wechat-download-api/         # clone 自 tmwgsicp/wechat-download-api
    ├── .env
    └── start.sh
```

---

## 常见错误

| 报错 / 现象 | 原因 | 处理 |
|-------------|------|------|
| `../wechat-download-api-main` 找不到 | 旧文档路径；或没 clone 第二个仓库 | 按第 2 步 clone `wechat-download-api`（注意没有 `-main` 后缀） |
| `env.example` / `start.sh` 找不到 | 人还不在 download-api 目录里 | 先 `cd .../wechat-download-api` |
| 未配置公众平台凭证 | 还没扫码或没 import | 完成第 5、6 步 |
| `need_session` / `auth:` | 登录过期 | 重新扫码并再次 import |
| `rate_limited:` / `freq control` / `ret=200013` | 历史接口被频控 | **停跑**，等 1–2h+，再用 `python run.py --profile safe history` |
| `history` 进度 `ok=0`、fail 猛涨、入库 0 | 连续频控空转（旧行为）或仍在硬跑 | 升级到本分支后应会熔断；仍见此象请 Ctrl+C 并换 `safe` |
| `import-list` → `inserted:0, skipped:N` | 名单已导入过 | 正常；看 `status` / `doctor` |
| `resolve` → `total:0` | 没有 pending | 正常；`failed` 需先 `retry-resolve` |
| 名单读不到 | `name_list.xlsx` 位置不对 | 放在 `wechat-work/name_list.xlsx`（与两个仓库同级） |
| 解析失败 | 昵称不完全一致 | 改成微信里显示的全名，或补上 `link`；然后 `retry-resolve` |
| `lsof -i :5000` 看到 `ControlCe` 占用，`bash start.sh` 报 `ERROR: [Errno 48] address already in use` | macOS 的“隔空播放接收器”（AirPlay Receiver）占用了 5000 端口，且系统进程会自动重启，无法靠 `kill` 彻底杀掉 | **方案一（推荐）**：系统设置 → 通用 → 隔空播放与接力 → 关闭「隔空播放接收器」，再重新运行 `bash start.sh`。<br>**方案二（备选）**：用 `PORT=5001 SITE_URL=http://127.0.0.1:5001 bash start.sh` 换端口启动，然后访问 `http://127.0.0.1:5001/login.html` |

---

## 可选：一键初始化脚本

若你已经完成第 2 步（两个仓库都在同级），可在爬虫目录执行：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
bash scripts/setup_from_scratch.sh
```

它会：创建虚拟环境、安装依赖、初始化数据库、生成上级目录的示例名单（若不存在）。  
**不会**替你启动 download-api 或扫码（这两步必须你自己做）。

---

## 说明

- 本项目只抓标题、作者、时间、正文等学术存档字段，不抓阅读量/点赞。  
- 请控制频率，仅用于学术研究与个人备份；**历史列表接口对频率极敏感**。  
- `wechat-download-api` 有自己的开源许可证（AGPL）；本爬虫通过读取其登录后的 `.env` 或 HTTP 接口与之配合。  
- 当前推荐跟踪分支：`cursor/wechat-archive-enhancements`（含熔断、doctor、进度条与安全档配置）。  
- 吞吐自检（可选）：`python scripts/benchmark_scale.py --rows 10000 --content-mock 200`
