# 微信公众号存档 · SQLite 离线库（使用指南）

本仓库**不向微信拉列表、也不下正文**。在线工作全部用同级的 **Schinza 源码**（`python main.py`）完成；这里只把已经归档的文件合并进原来的 `wechat_archive.db`。

不要下载 Schinza 的发行版 App。本流程依赖 fork 分支 `scalable-archive`（版本 1.9.2）的源码界面。

```text
分工
  Schinza 源码   → 抓 30 分钟凭证、翻页拉列表、慢速下正文
  本仓库         → import-list + import-schinza-export → SQLite / JSONL
```

```text
wechat-work/
├── name_list.xlsx
├── wechat_crawler/                         ← 本仓库
└── schinza-wechat-certificate-main/        ← Schinza 源码
```

当前跟踪分支：`cursor/wechat-archive-enhancements`。

---

## 你需要提前准备

1. Git、Python 3.11+
2. 已登录的微信**桌面**客户端，且只处理你有权存档的公众号
3. 同级目录里已按 [Schinza 中文指南](https://github.com/MichaelMu151/schinza-wechat-certificate/blob/scalable-archive/README.zh-CN.md) 用源码跑通抓包

Intel Mac 跑 Schinza GUI 必须用 python.org 的 Python 3.14 虚拟环境 `.venv-intel`（Homebrew 3.13 没有 `_tkinter`）。本仓库的 CLI 不需要 Tk，用普通 `.venv` 即可。

---

## 第 1 步：工作目录与两个仓库

```bash
mkdir -p "$HOME/Desktop/wechat-work"
cd "$HOME/Desktop/wechat-work"

git clone https://github.com/MichaelMu151/wechat_crawler.git
cd wechat_crawler
git switch cursor/wechat-archive-enhancements
cd ..

git clone --branch scalable-archive \
  https://github.com/MichaelMu151/schinza-wechat-certificate.git \
  schinza-wechat-certificate-main
```

以后更新（两个目录都要 pull，不要切到 Schinza 上游 `main`）：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler" && git pull && cd ..
cd "$HOME/Desktop/wechat-work/schinza-wechat-certificate-main" && git pull && cd ..
```

---

## 第 2 步：名单 `name_list.xlsx`

放在两个仓库的**上一级**（默认路径写在 `config.yaml` 的 `paths.name_list`）。

| nickname | link |
|----------|------|
| 云南省第一人民医院 | https://mp.weixin.qq.com/s/xxxxx |

- `nickname` 必须与 Schinza 卡片上的公众号名称**逐字一致**（含空格、医院全称）。
- `link` 是该号任意一篇公开文章，便于核对；没有链接也可以先导入名单。

生成示例表：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
python3 scripts/create_name_list_example.py --out ../name_list.xlsx
```

---

## 第 3 步：安装本仓库（离线库）

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python run.py init-db
```

以后每次先：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
source .venv/bin/activate
```

提示符前出现 `(.venv)` 即可。可选一键脚本 `bash scripts/setup_from_scratch.sh` 会建 venv、装依赖、init-db；**不会**启动 Schinza 或操作微信。

---

## 第 4 步：用 Schinza 源码完成全部在线工作

详细逐步操作（安装 CA、重启微信、添加公众号、30 分钟窗口只拉列表）见：

**https://github.com/MichaelMu151/schinza-wechat-certificate/blob/scalable-archive/README.zh-CN.md**

本地启动（Intel Mac）：

```bash
cd "$HOME/Desktop/wechat-work/schinza-wechat-certificate-main"
.venv-intel/bin/python main.py
```

Apple Silicon 用 `.venv-mac/bin/python main.py`；Windows 用 `.venv\Scripts\python.exe main.py`。  
确认版本 `1.9.2`，历史页按钮是 **拉取列表并归档** / **继续归档正文**。不要打开 Applications 里的 `Schinza.app`。

和本仓库衔接时只要记住：

1. 名称与 `name_list.xlsx` 完全一致。
2. **30 分钟凭证只用来翻页拉列表**；正文不消耗凭证。
3. 列表没完就续约再点「拉取列表并归档」；过期后可点「继续归档正文」。
4. 产物在：

```text
schinza-wechat-certificate-main/data/archives/公众号名/
├── manifest.json
├── manifest.jsonl
└── articles/
```

遇到 `unknownerror` / 429 /「访问过于频繁」：在 Schinza 里停，等数小时到一天，同一账号续跑。不要在本仓库里再跑在线 `history` / `content`。

---

## 第 5 步：离线合并到 SQLite

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
source .venv/bin/activate
python run.py import-list
```

`inserted:0, skipped:N` 表示名单已经在库里，属于正常。

先 dry-run（只报告、不写库）：

```bash
python run.py import-schinza-export \
  --manifest "../schinza-wechat-certificate-main/data/archives/公众号名称/manifest.json" \
  --articles-dir "../schinza-wechat-certificate-main/data/archives/公众号名称/articles" \
  --dry-run
```

确认输出里有 `matched_account=true`、篇数合理，再去掉 `--dry-run`：

```bash
python run.py import-schinza-export \
  --manifest "../schinza-wechat-certificate-main/data/archives/公众号名称/manifest.json" \
  --articles-dir "../schinza-wechat-certificate-main/data/archives/公众号名称/articles"
python run.py status
python run.py export-jsonl
python run.py export-account-summary
```

该命令**完全离线**，不访问微信。按规范化文章 URL 去重；已有 `ok` 正文不会覆盖，只补新文章或旧库缺失的正文。超大号若 `manifest.json` 里没有嵌套 `articles`，以同目录 `manifest.jsonl` 为准（命令仍指向 `manifest.json` 即可）。

| 路径 | 内容 |
|------|------|
| `data/wechat_archive.db` | SQLite 主库（与旧爬虫同一套表） |
| `export/articles.jsonl` | 默认导出 `status=ok` 的文章 |
| `export/account_summary.jsonl` | 按账号篇数、时间跨度、状态汇总 |

### 如何读 `python run.py status`

上面一张表是**公众号**，下面一张是**文章**。离线导入成功后，常见情况是：

- 账号：`resolve` 能对上名称，文章以 `ok` 为主（正文来自 Schinza 的 Markdown）。
- 若 `matched_account=false`：两边名称不一致，改 Excel 或 Schinza 卡片后重新 `import-list` 再导入。
- `doctor` 给出的下一步应指向「去 Schinza 归档 + `import-schinza-export`」，而不是 `python run.py history`。

---

## 第 6 步：用 Python 分析

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

## 本仓库命令一览

**日常使用**

| 命令 | 作用 |
|------|------|
| `init-db` | 初始化 SQLite |
| `import-list` | 导入 `name_list.xlsx` |
| `import-schinza-export` | 离线合并 Schinza 归档目录 |
| `status` / `doctor` / `errors` | 看库内进度 |
| `export-jsonl` / `export-account-summary` | 导出 |
| `backup-db` / `maintain-db` | 备份与维护 |
| `schinza-status` | 只读检查 Schinza `accounts.json` 的计数（不显示密钥） |

**已停用的在线抓取**

`history`、`content`、`resolve`、`pilot`、`serve` 等默认会报错，提示改走 Schinza 源码。它们曾让文档看起来像「还要再向微信请求一遍」，和 Schinza 重复，也更容易撞频控。

旧脚本若仍调用它们：

```bash
python run.py --force-legacy history
```

不建议作为默认流程。

---

## 常见错误

| 现象 | 处理 |
|------|------|
| `matched_account=false` | 公众号名与 Excel / Schinza 不完全一致 |
| `import-list` → `inserted:0, skipped:N` | 名单已导入过，正常 |
| 运行 `history` / `content` 被拒绝 | 预期行为；用 Schinza 源码拉完再 `import-schinza-export` |
| Schinza `No module named '_tkinter'` | Intel Mac 改用 `.venv-intel`（见 Schinza 指南第 3.1 节） |
| Schinza 捕获不到凭证 | 完全退出并重启微信，重新打开文章或滚动历史页 |
| 退出 Schinza 后网络异常 | 关闭系统手动代理 `127.0.0.1:8088` |

---

## 说明

- 只存标题、作者、时间、正文等学术存档字段，不抓阅读量/点赞。
- Schinza 使用 MIT 许可证；本仓库只读其归档目录与（可选）`accounts.json`，不会把 `uin`/`key` 写入 SQLite。
- `config.yaml` 里仍有 `platform.schinza_accounts_path`，仅供 `schinza-status` 等只读检查；默认在线抓取路径已关闭。
