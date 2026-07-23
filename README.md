# 微信公众号存档爬虫 · 新手操作手册

面向：第一次使用本工具的同学。  
目标：导入名单 → 登录公众平台 → 拉历史文章 → 抓正文 → 导出给 Python 分析。

> 若你把项目放在别的目录，把文中路径里的文件夹名改成你自己的即可。  
> **关键原则：不要用含糊的 `../xxx`，优先复制完整绝对路径。**

---

## 0. 先看懂：爬虫 和 扫码工具 是两个文件夹

典型布局（以本机为例）：

| 用途 | 示例完整路径 |
|------|----------------|
| **爬虫项目** | `/Users/mushiquan/Desktop/Master Thesis/新微信公众号` |
| **扫码登录工具** | `/Users/mushiquan/Desktop/Master Thesis/微信公众号/wechat-download-api-main` |

注意：上面两个路径里，一个叫 **「新微信公众号」**，一个叫 **「微信公众号」**，名字不一样。

GitHub 仓库克隆后，爬虫目录也可能叫 `wechat_crawler` / `wechat_archive`，逻辑相同。

---

## 你若看到 `cd: no such file or directory: ../wechat-download-api-main`

说明你在错误的位置用了相对路径。

例如人在：

```text
.../新微信公众号
```

却执行：

```bash
cd "../wechat-download-api-main"
```

会去找不存在的：

```text
.../Master Thesis/wechat-download-api-main
```

于是后面的 `cp env.example`、`bash start.sh` 也会连锁失败。  
`# 编辑 .env：...` 是注释说明，不要当命令执行。

**正确做法：用绝对路径进入登录工具目录**（见第 3 节）。

---

## 1. 开始前准备什么

1. 能上网的电脑  
2. Python 3（终端执行 `python3 --version` 能出版本号）  
3. **你自己的一个微信公众号**（订阅号即可）+ 该号**管理员微信**（扫码用）  
4. 名单 `name_list.xlsx`，表头：

| nickname | link |
|----------|------|
| 云南省第一人民医院 | https://mp.weixin.qq.com/s/xxxxx |

- `nickname` 必须与微信显示名**完全一致**  
- `link` 为该号任意一篇文章链接（可短链）；没有也能试，有链接更稳  

---

## 2. 第一次安装爬虫（只需一次）

```bash
cd "/Users/mushiquan/Desktop/Master Thesis/新微信公众号"

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip
pip install -r requirements.txt

python run.py init-db
```

以后每次开工先执行：

```bash
cd "/Users/mushiquan/Desktop/Master Thesis/新微信公众号"
source .venv/bin/activate
```

---

## 3. 启动扫码登录工具（拉历史前必做）

### 3.1 启动并扫码

```bash
cd "/Users/mushiquan/Desktop/Master Thesis/微信公众号/wechat-download-api-main"

cp env.example .env
```

用编辑器打开 `.env`，把 `SITE_URL` 改成：

```text
SITE_URL=http://127.0.0.1:5000
```

保存后：

```bash
bash start.sh
```

浏览器打开：**http://127.0.0.1:5000/login.html**  
用**公众号管理员微信**扫码。登录成功后，该终端可先保持运行。

### 3.2 把凭证导入爬虫

```bash
cd "/Users/mushiquan/Desktop/Master Thesis/新微信公众号"
source .venv/bin/activate

python run.py import-platform-from-download-api \
  --env "/Users/mushiquan/Desktop/Master Thesis/微信公众号/wechat-download-api-main/.env"

python run.py platform-status
```

凭证约 **4 天**过期；过期后重新扫码并再执行一次 import。

---

## 4. 正式爬取（按顺序）

```bash
cd "/Users/mushiquan/Desktop/Master Thesis/新微信公众号"
source .venv/bin/activate

python run.py import-list --file name_list.xlsx
python run.py resolve --limit 2
python run.py history --limit 1
python run.py content --limit 10
python run.py status
python run.py export-jsonl
```

小规模跑通后，去掉 `--limit` 再做全量。

结果位置：

- 数据库：`data/wechat_archive.db`  
- 导出：`export/articles.jsonl`  

---

## 5. Python 读库示例

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

## 6. 常见问题

| 现象 | 怎么办 |
|------|--------|
| `../wechat-download-api-main` 找不到 | 改用第 3 节绝对路径 |
| 未配置公众平台凭证 | 先完成第 3 节扫码 + import |
| need_session / 登录过期 | 重新扫码并再次 import |
| 解析失败 | 核对昵称完全一致，或补 `link` |
| 访问频繁 | 等待后重试；可增大 `config.yaml` 的 `sleep_min/max` |

---

## 7. 记住这个顺序

```text
启动并登录 wechat-download-api
  → 爬虫里 import 凭证
  → import-list → resolve → history → content → export
```

不要把「爬虫目录」和「扫码工具目录」混成一个文件夹来找 `env.example` / `start.sh`。
