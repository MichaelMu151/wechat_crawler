# 微信公众号存档 · SQLite 离线库

本仓库**不再负责向微信拉列表或正文**。在线工作全部在 Schinza 里完成；这里只把
Schinza 已经归档的结果，离线合并进原来的 `wechat_archive.db`，并提供 status / 导出。

```text
分工
  Schinza   → 抓 30 分钟凭证、拉历史列表、慢速下正文
  本仓库    → import-list + import-schinza-export → SQLite / JSONL
```

两个 GitHub 仓库是分开的，请放在同一工作目录：

```text
wechat-work/
├── name_list.xlsx
├── wechat_crawler/                         ← 本仓库
└── schinza-wechat-certificate-main/        ← Schinza
```

---

## 你需要提前准备

1. Git、Python 3.11+
2. 已登录的微信桌面客户端，且只处理你有权存档的公众号
3. Schinza（源码或发行版均可）

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

之后更新：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler" && git pull && cd ..
cd "$HOME/Desktop/wechat-work/schinza-wechat-certificate-main" && git pull && cd ..
```

---

## 第 2 步：名单 `name_list.xlsx`

表头必须是两列，名称必须与 Schinza 里填写的公众号名**逐字一致**：

| nickname | link |
|----------|------|
| 云南省第一人民医院 | https://mp.weixin.qq.com/s/xxxxx |

也可生成示例表：

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
python3 scripts/create_name_list_example.py --out ../name_list.xlsx
```

---

## 第 3 步：安装本仓库

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python run.py init-db
```

可选：`bash scripts/setup_from_scratch.sh`（建 venv、装依赖、init-db、必要时生成示例名单）。
它**不会**启动 Schinza 或操作微信。

---

## 第 4 步：在 Schinza 里完成在线工作

凭证捕获、CA、代理流程见 Schinza 自己的 README，不要改。这里只说明和效率有关的部分：

1. 在「凭证管理」抓到该号凭证（约 **30 分钟**有效）。
2. 打开「历史文章」，选该号，点 **拉取列表并归档**。
3. **这 30 分钟只用来翻页拉列表**（getmsg 需要 `uin`/`key`）。正文是公开 HTML，**不消耗凭证**。
4. 列表未拉完而窗口结束：立即续约，再点一次继续翻页。不要在列表还没完时去下正文。
5. 列表拉完后，Schinza 会自动开始慢速下正文（单请求、每篇 8–15 秒）。
6. 凭证已经过期、但列表已在本地：点 **继续归档正文**。过期的 cookie 不会带上，避免把公开页打成登录页。

结果在 Schinza 目录：

```text
schinza-wechat-certificate-main/data/archives/公众号名/
├── manifest.json
├── manifest.jsonl
└── articles/          # Markdown / HTML / …
```

遇到 `unknownerror` / 429 /「访问过于频繁」：先停，等数小时到一天，再续跑同一账号。
已完成正文会跳过。不要提高并发。

---

## 第 5 步：离线合并到 SQLite

```bash
cd "$HOME/Desktop/wechat-work/wechat_crawler"
source .venv/bin/activate
python run.py import-list
```

先 dry-run：

```bash
python run.py import-schinza-export \
  --manifest "../schinza-wechat-certificate-main/data/archives/公众号名称/manifest.json" \
  --articles-dir "../schinza-wechat-certificate-main/data/archives/公众号名称/articles" \
  --dry-run
```

确认 `matched_account=true` 后去掉 `--dry-run` 正式写入：

```bash
python run.py import-schinza-export \
  --manifest "../schinza-wechat-certificate-main/data/archives/公众号名称/manifest.json" \
  --articles-dir "../schinza-wechat-certificate-main/data/archives/公众号名称/articles"
python run.py status
python run.py export-jsonl
python run.py export-account-summary
```

该命令完全离线，不访问微信。按规范化文章 URL 去重；已有正文不会覆盖，只补新文章或旧库缺失的正文。

| 路径 | 内容 |
|------|------|
| `data/wechat_archive.db` | SQLite 主库（与旧爬虫同一套表） |
| `export/articles.jsonl` | 默认导出 `status=ok` 的文章 |
| `export/account_summary.jsonl` | 按账号篇数、时间跨度、ok/failed 汇总 |

`python run.py status` / `doctor` 看入库漏斗和建议。建议动作指向 Schinza 与
`import-schinza-export`，而不是再跑在线 `history` / `content`。

---

## 用 Python 分析

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

## 本仓库还保留哪些命令

**日常使用**

| 命令 | 作用 |
|------|------|
| `init-db` | 初始化 SQLite |
| `import-list` | 导入 `name_list.xlsx` |
| `import-schinza-export` | 离线合并 Schinza 归档 |
| `status` / `doctor` / `errors` | 看库内进度 |
| `export-jsonl` / `export-account-summary` | 导出 |
| `backup-db` / `maintain-db` | 备份与维护 |
| `schinza-status` | 只读检查 Schinza `accounts.json` 计数（不显示密钥） |

**已停用的在线抓取**（`history` / `content` / `resolve` / `pilot` / `serve` 等）

这些命令会直接报错，提示改走 Schinza。它们曾让 README 看起来像「爬虫还要再向微信请求一遍」，
和 Schinza 重复、也更容易撞频控。若旧脚本仍依赖它们：

```bash
python run.py --force-legacy history
```

不建议作为默认流程。

---

## 常见错误

| 现象 | 处理 |
|------|------|
| `matched_account=false` | 公众号名与 `name_list.xlsx` / Schinza 不完全一致 |
| `import-list` → `inserted:0, skipped:N` | 名单已导入过，正常 |
| 在线 `history` / `content` 被拒绝 | 预期行为；去 Schinza 拉，再 `import-schinza-export` |
| Schinza 捕获不到凭证 | 重启微信，重新打开文章或滚动历史页 |
| 退出 Schinza 后网络异常 | 关闭系统手动代理 `127.0.0.1:8088` |

---

## 说明

- 只存标题、作者、时间、正文等学术存档字段，不抓阅读量/点赞。
- 当前跟踪分支：`cursor/wechat-archive-enhancements`。
- Schinza 使用 MIT 许可证；本仓库只读其归档目录与（可选）`accounts.json`。
