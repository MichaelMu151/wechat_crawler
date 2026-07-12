# 微信公众号学术存档与管理系统

面向硕论/学术存档：导入公众号名单或公开文章链接，在严格限频下抓取结构化文本，持久化到 **SQLite**，并通过 API 和本地管理台查看任务与内容。

## 能做什么

1. 从 `name_list.xlsx` 导入账号  
2. 用样例短链/长链解析 `__biz` 与公众号名  
3. （需微信会话）分页拉取历史发文列表  
4. 抓取正文 HTML + 纯文本，失败自动退避重试
5. 失效文标记为 `deleted`  
6. 崩溃后恢复历史分页和未完成任务
7. FastAPI、SSE 任务事件与 React 本地管理台
8. 导出 JSONL，方便后续 Python/pandas 分析  

**不做：** 验证码绕过、无授权的高频抓取、阅读量/点赞抓取。任意公开账号的历史完整性不作保证。

## 环境

```bash
cd wechat_archive
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

开发测试可使用：

```bash
pip install -e ".[dev]"
pytest
```

名单默认读取上级目录的 `../name_list.xlsx`（可在 `config.yaml` 修改）。

## 快速试点（无需会话）

先验证「名单 → 解析账号 → 样例正文」：

```bash
python run.py pilot
# 或分步：
python run.py init-db
python run.py import-list
python run.py resolve
python run.py content
python run.py status
```

成功后可在 `data/wechat_archive.db` 查看；或：

```bash
python run.py export-jsonl
```

## API 与管理台

后端默认仅监听本机，启动后 API 文档位于 <http://127.0.0.1:8000/api/docs>：

```bash
python run.py serve
```

前端开发：

```bash
cd web_ui
npm install
npm run dev
# 另一个终端在项目根目录运行：python run.py serve
# 浏览器访问 http://127.0.0.1:8000
```

生产构建后，`serve` 会直接托管 `web_ui/dist`：

```bash
cd web_ui && npm run build && cd ..
python run.py serve
```

也可以一键运行：

```bash
docker compose up --build
```

管理台支持仪表盘、名单任务、公开链接归档、文章检索与隔离预览、任务状态和会话健康概览。会话 Cookie 不会通过 API 返回。

## 拉取历史列表（需要微信登录态）

历史接口依赖个人微信在「查看历史消息」时的参数。你可挂 1–3 个微信号。

### 抓包取参（PC 微信最省事）

1. 安装 [Charles](https://www.charlesproxy.com/) / mitmproxy / Fiddler，配置 HTTPS 解密  
2. 手机或电脑微信走该代理  
3. 打开任意已关注公众号 → **查看历史消息**，再往下翻一页  
4. 在代理里找到请求：  
   `mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz=...&uin=...&key=...`  
5. 复制：  
   - `uin`  
   - `key`  
   - 请求头里的整段 `Cookie`  

### 写入会话文件

```bash
cp sessions/example_session.yaml sessions/session_1.yaml
# 可再复制 session_2.yaml / session_3.yaml
```

编辑填入 `uin` / `key` / `cookie`，保存后：

```bash
python run.py history
python run.py content
python run.py status
```

`key` 会过期；若报错或大量失败，重新抓包更新会话即可。

## 常用命令

| 命令 | 作用 |
|------|------|
| `python run.py status` | 进度概览 |
| `python run.py import-list` | 重新导入名单（已存在则跳过） |
| `python run.py resolve --limit 3` | 只解析前 N 个 |
| `python run.py history --limit 1` | 只拉 1 个号的历史 |
| `python run.py content --limit 20` | 只抓 20 篇正文 |
| `python run.py retry-failed --limit 20` | 将失败正文重新入队 |
| `python run.py serve` | 启动 API 与管理台 |
| `python run.py export-jsonl` | 导出 `export/articles.jsonl` |

## 数据库字段（核心）

**accounts：** 名单昵称、样例链接、`biz`、解析/列表状态  

**articles：** 标题、作者、摘要、发布时间、URL、封面、`content_html`、`content_text`、`status`  

文章 `status`：`listed` → `ok` / `deleted` / `retry_wait` / `failed` / `out_of_range`

`jobs` 与 `job_events` 记录持久任务和事件；`crawl_checkpoints` 保存历史分页进度。启动时会回收意外中断的 `running` 状态。

## 后续用 Python 分析

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

## 说明

- 仅供学术存档；请控制频率，配置里默认间隔约 4–9 秒  
- 微信接口可能变更；解析失败时优先更新会话或反馈样例 HTML  
- 时间窗在 `config.yaml` 的 `crawl.start_date` / `end_date`
- `sessions/session_*.yaml` 含账号凭据，已被 Git 忽略；不要上传、复制到日志或通过公网暴露 API
- 遇到验证码、环境异常或明确封禁时应暂停并人工检查，而不是提高并发或规避验证
- 历史列表使用单 worker；公开正文也应保持小规模并发，以账号安全与归档质量优先
