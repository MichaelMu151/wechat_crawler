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

## 第 4 步：安装爬虫（只需一次）

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
python run.py import-list

# 2) 先小规模试跑
python run.py resolve --limit 2
python run.py history --limit 1
python run.py content --limit 10
python run.py status

# 3) 确认无误后，去掉 --limit 做全量
# python run.py resolve
# python run.py history
# python run.py content

# 4) 导出
python run.py export-jsonl
```

结果位置（均在爬虫目录内）：

- 数据库：`data/wechat_archive.db`  
- 导出：`export/articles.jsonl`  

---

## 第 8 步：用 Python 分析

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

## 一键对照：正确目录长这样

```text
wechat-work/
├── name_list.xlsx
├── wechat_crawler/              # clone 自 MichaelMu151/wechat_crawler
│   ├── .venv/
│   ├── config.yaml              # 默认指向 ../name_list.xlsx 与 ../wechat-download-api/.env
│   ├── run.py
│   └── data/wechat_archive.db
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
| `need_session` | 登录过期 | 重新扫码并再次 import |
| 名单读不到 | `name_list.xlsx` 位置不对 | 放在 `wechat-work/name_list.xlsx`（与两个仓库同级） |
| 解析失败 | 昵称不完全一致 | 改成微信里显示的全名，或补上 `link` |

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
- 请控制频率，仅用于学术研究与个人备份。  
- `wechat-download-api` 有自己的开源许可证（AGPL）；本爬虫通过读取其登录后的 `.env` 或 HTTP 接口与之配合。
