# 微信公众号学术存档与管理系统

本项目用于硕士论文、学术研究和个人内容备份。它可以导入公众号名单或单篇公开文章链接，抓取文章标题、作者、时间、HTML 和纯文本，并保存到本地 SQLite 数据库。项目同时提供命令行、HTTP API 和本地 Web 管理台。

> 重要：本项目不会替你登录微信，也不会控制微信客户端。“挂微信号”在本文档中是指：由使用者在自己的微信客户端中正常登录，通过抓包取得短期有效的 `uin`、`key` 和 `Cookie`，再将它们写入本地会话文件。凭据过期后需要重新获取。

## 1. 功能和限制

### 可以做什么

- 从 Excel 导入“公众号名称 + 一篇样例文章链接”
- 从样例文章解析公众号唯一标识 `__biz`
- 使用有效微信会话分页拉取公众号历史文章列表
- 直接归档用户粘贴的单篇公开文章
- 抓取正文 HTML 和纯文本
- 对网络错误执行有限次数的自动重试和退避
- 记录失败、删除、超出研究时间范围等状态
- 保存历史分页检查点，进程中断后可以继续
- 通过 Web 管理台创建任务、查看状态、搜索和预览文章
- 导出 JSONL，供 Python、R 或其他研究工具使用

### 当前不做什么

- 不破解或绕过验证码、风控页面和访问限制
- 不保证获取任意公众号的完整历史
- 不抓取阅读量、点赞量、评论或私密数据
- 不提供微信公众号后台管理员登录
- 不会自动刷新已经过期的微信 `key` 和 Cookie
- 不建议作为公网多用户服务直接部署

## 2. 使用前需要准备什么

### 必需软件

- Python 3.11 或更高版本
- Git
- 若使用 Web 管理台：Node.js 20 或更高版本
- 若使用 Docker：Docker Desktop 与 Docker Compose

检查版本：

```bash
python3 --version
node --version
git --version
```

### 使用者需要提供的数据

根据使用场景，准备以下内容：

1. 单篇文章归档：提供公开的 `https://mp.weixin.qq.com/...` 文章链接。
2. 批量名单归档：提供一个 Excel 文件，每个公众号至少包含名称和一篇公开样例文章链接。
3. 获取公众号历史：除 Excel 名单外，还需要使用者自己的微信会话参数：
   - `uin`
   - `key`
   - 完整请求头 `Cookie`

第三项只在运行历史列表任务时需要。仅抓取公开单篇文章不需要微信会话。

## 3. 安装

### 3.1 下载并进入项目

```bash
git clone https://github.com/MichaelMu151/wechat_crawler.git
cd wechat_crawler
git switch cursor/wechat-archive-enhancements
```

如果代码已经在本机，直接进入含有 `run.py` 的目录。

### 3.2 创建 Python 虚拟环境

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

Windows PowerShell：

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

看到命令行前出现 `(.venv)`，表示虚拟环境已经启用。

### 3.3 初始化数据库

```bash
python run.py init-db
python run.py status
```

数据库默认保存在：

```text
data/wechat_archive.db
```

## 4. 三种使用方式

### 方式 A：归档一篇公开文章，不挂微信号

适合先验证程序是否正常，或只保存少量已知文章。

1. 构建并启动管理台：

   ```bash
   cd web_ui
   npm install
   npm run build
   cd ..
   python run.py serve
   ```

2. 浏览器打开 <http://127.0.0.1:8000>。
3. 在“粘贴公开的 `mp.weixin.qq.com` 文章链接”输入框中粘贴文章地址。
4. 点击“归档链接”。
5. 管理台会创建正文任务。等待状态由 `listed` 或 `running` 变为 `ok`。
6. 在“文章库”点击文章即可预览。

可以输入：

```text
https://mp.weixin.qq.com/s/xxxxxxxxxxxx
```

或者带有 `__biz`、`mid`、`idx`、`sn` 参数的长链接。程序会移除 `scene`、`from` 等追踪参数。

### 方式 B：用 Excel 名单抓取样例文章，不挂微信号

这条路径可以验证“名单 → 账号解析 → 样例正文”，但不会自动获得完整历史。

#### 第一步：制作 Excel

第一行必须是列名。推荐使用：

| nickname | link |
|---|---|
| 示例公众号 A | `https://mp.weixin.qq.com/s/文章链接A` |
| 示例公众号 B | `https://mp.weixin.qq.com/s/文章链接B` |

要求：

- `nickname`：必需，填写公众号名称。
- `link`：建议必填，填写该公众号任意一篇可正常打开的公开文章。
- 链接列也可以命名为 `sample_url` 或 `url`。
- 每一行只填写一个公众号。
- 不要在单元格中只放超链接显示文本；单元格值需要是完整 URL。
- 样例文章最好来自目标公众号本身，不要使用转载后的链接。

默认配置读取项目上级目录的：

```text
../name_list.xlsx
```

若 Excel 位于项目目录内，例如 `input/name_list.xlsx`，修改 `config.yaml`：

```yaml
paths:
  name_list: input/name_list.xlsx
```

#### 第二步：运行试点

```bash
python run.py pilot --limit 3
```

该命令会：

1. 导入名单。
2. 解析最多 3 个公众号。
3. 若没有配置会话，则跳过历史列表。
4. 抓取样例文章正文。

确认结果：

```bash
python run.py status
python run.py export-jsonl
```

试点成功后再去掉 `--limit 3`：

```bash
python run.py pilot
```

### 方式 C：挂自己的微信号并获取历史列表

这是最容易遇到会话过期和微信风控的步骤。请先完成方式 B，确认名单、数据库和正文抓取均正常。

#### “挂微信号”究竟是什么

本项目需要复用微信客户端在打开“公众号历史消息”时产生的请求参数。项目只读取三个值：

- `uin`：当前微信会话的用户标识参数
- `key`：短期有效的访问参数
- `cookie`：请求头中的完整 Cookie

这些值不是公众号管理员账号密码，也不是微信登录密码。它们仍然属于敏感登录凭据，泄露后可能被他人冒用，因此只能保存在本机。

为降低风险：

- 使用你本人控制的微信号。
- 目标公众号应当能够在该微信号中正常打开和查看历史。
- 建议先只配置一个微信号。
- 不要将会话文件发给任何人。
- 不要把 `uin`、`key` 或 Cookie 粘贴到 Issue、聊天记录或截图中。

## 5. 使用 Charles 获取微信会话

下面以“macOS 电脑运行 Charles，手机微信连接同一 Wi-Fi”为例。Charles 的界面可能随版本略有变化。

### 5.1 准备工作

1. 电脑和手机连接同一个可信 Wi-Fi。
2. 安装并打开 [Charles](https://www.charlesproxy.com/)。
3. 在 Charles 中查看代理端口，默认通常是 `8888`：
   - `Proxy` → `Proxy Settings`
4. 获取电脑局域网 IP，例如 `192.168.1.20`：

   ```bash
   ipconfig getifaddr en0
   ```

   若没有输出，可在 macOS“系统设置 → 网络 → Wi-Fi → 详细信息”查看 IP。

### 5.2 设置手机 Wi-Fi 代理

以 iPhone 为例：

1. 打开“设置 → Wi-Fi”。
2. 点击当前 Wi-Fi 右侧的信息按钮。
3. 找到“配置代理”。
4. 选择“手动”。
5. 服务器填写电脑局域网 IP，例如 `192.168.1.20`。
6. 端口填写 Charles 端口，例如 `8888`。
7. 暂不填写用户名和密码。
8. 保存。
9. Charles 首次收到手机请求时可能弹出授权窗口，选择允许。

Android 的操作名称可能是“修改网络 → 高级选项 → 代理 → 手动”，服务器和端口填写方式相同。

### 5.3 安装并信任 Charles 证书

只有在使用者理解 HTTPS 解密风险并确认当前网络可信时才进行。

iPhone：

1. 保持手机代理已连接。
2. 用 Safari 打开 `https://chls.pro/ssl`。
3. 下载 Charles 证书描述文件。
4. 打开“设置 → 通用 → VPN 与设备管理”，安装该描述文件。
5. 打开“设置 → 通用 → 关于本机 → 证书信任设置”。
6. 为 Charles 证书启用完全信任。

Android：

1. 用浏览器打开 `https://chls.pro/ssl` 下载证书。
2. 按系统提示安装为“VPN 和应用”使用的 CA 证书。
3. 部分 Android 或微信版本可能不接受用户 CA；遇到此情况不要尝试绕过系统安全机制，可改用其他受支持的抓包方式。

### 5.4 为微信域名开启 SSL Proxying

在 Charles 中：

1. 打开 `Proxy` → `SSL Proxying Settings`。
2. 勾选 `Enable SSL Proxying`。
3. 添加：

   ```text
   Host: mp.weixin.qq.com
   Port: 443
   ```

4. 清空旧记录或开始一个新的 Recording，方便查找。

### 5.5 在微信中产生历史列表请求

1. 在手机打开微信。
2. 打开目标公众号。
3. 进入公众号资料页或历史消息页。
4. 点击“查看历史消息”。
5. 等待文章列表出现。
6. 向下滚动一至两页。
7. 回到 Charles。

在 Charles 中筛选：

```text
Host: mp.weixin.qq.com
Path: /mp/profile_ext
Query: action=getmsg
```

目标请求通常类似：

```text
https://mp.weixin.qq.com/mp/profile_ext?action=getmsg&__biz=...&f=json&offset=10&count=10&uin=...&key=...
```

不要选择只有 `action=home` 的页面请求；需要选择 `action=getmsg` 的翻页请求。

### 5.6 从请求中复制三个值

选中请求后查看 Request：

1. 在 Query String 中复制 `uin` 的值。
2. 在 Query String 中复制 `key` 的完整值。
3. 在 Headers 中找到 `Cookie`，复制其完整值。

复制时注意：

- 不要包含字段名，例如 `uin=` 不需要写入值中。
- 不要截断 `key`。
- Cookie 需要复制整行值，而不是只复制某一个 Cookie 项。
- 不要复制 Response Headers 中的 `Set-Cookie` 来代替 Request Cookie。
- 不要把这些内容截图或提交到 Git。

### 5.7 操作完成后恢复手机网络

抓包结束后立即：

1. 将手机 Wi-Fi 的“配置代理”改回“关闭”。
2. 在不再需要抓包时删除或取消信任 Charles 根证书。
3. 关闭 Charles SSL Proxying。

这一步很重要。长期保留可解密 HTTPS 的代理和根证书会增加安全风险。

## 6. 将会话写入项目

### 6.1 创建会话文件

在项目根目录执行：

```bash
cp sessions/example_session.yaml sessions/session_1.yaml
```

Windows PowerShell：

```powershell
Copy-Item sessions\example_session.yaml sessions\session_1.yaml
```

用文本编辑器打开 `sessions/session_1.yaml`：

```yaml
name: my_wechat_1
enabled: true
uin: "在这里填写 uin 的值"
key: "在这里填写 key 的值"
cookie: "在这里填写完整 Request Cookie"
note: "2026-07-12 从手机微信历史消息请求获取"
```

输入规则：

- 三个值都建议使用双引号包裹。
- 若 Cookie 中本身出现双引号，需要进行 YAML 转义，或改用单引号包裹整个值。
- `enabled: false` 会停用该会话。
- 文件名必须位于 `sessions/` 下并以 `.yaml` 结尾。
- 不要修改 `example_session.yaml`；应复制为新文件。
- `session_*.yaml` 已被 `.gitignore` 排除，不会进入正常 Git 提交。

### 6.2 确认程序读取到了会话

```bash
python run.py status
```

输出应显示：

```text
可用会话数: 1
```

若仍然显示 `0`，依次检查：

1. 文件是否位于项目的 `sessions/` 目录。
2. 文件扩展名是否为 `.yaml`。
3. 文件名是否仍是 `example_session.yaml`，示例文件会被忽略。
4. `enabled` 是否为 `true`。
5. `uin`、`key`、`cookie` 是否为空或仍含 `REPLACE_ME`。
6. YAML 缩进和引号是否正确。

### 6.3 先用一个公众号验证

```bash
python run.py history --limit 1
python run.py status
```

如果账号列表状态变为 `done`，说明历史接口可用。然后抓取正文：

```bash
python run.py content --limit 10
python run.py status
```

确认 10 篇文章能够正常归档后，再扩大范围：

```bash
python run.py history
python run.py content
```

## 7. 挂多个微信号

多个微信号用于轮换会话，不代表可以提高并发。历史列表接口仍然默认单任务运行。

分别从不同微信号的真实客户端请求中取得各自参数，然后创建：

```text
sessions/session_1.yaml
sessions/session_2.yaml
sessions/session_3.yaml
```

每个文件的 `name` 应不同：

```yaml
name: research_wechat_1
enabled: true
uin: "..."
key: "..."
cookie: "..."
```

当前会话池按顺序轮换使用。若某个会话过期，可以暂时设置：

```yaml
enabled: false
```

然后重新运行任务。建议最多先配置 1–3 个会话，避免账号安全风险。

## 8. 运行完整批量归档

推荐按阶段运行，便于定位问题：

```bash
# 1. 初始化数据库
python run.py init-db

# 2. 导入 Excel
python run.py import-list

# 3. 从样例文章解析公众号信息
python run.py resolve --limit 3

# 4. 小范围测试历史列表
python run.py history --limit 1

# 5. 小范围测试正文
python run.py content --limit 10

# 6. 检查状态
python run.py status

# 7. 确认无异常后扩大范围
python run.py resolve
python run.py history
python run.py content

# 8. 导出
python run.py export-jsonl
```

不要在会话刚配置完成后直接使用激进并发或同时启动多个 `history` 进程。

## 9. Web 管理台

### 9.1 首次构建

```bash
cd web_ui
npm install
npm run build
cd ..
```

### 9.2 启动

```bash
source .venv/bin/activate
python run.py serve
```

浏览器打开：

- 管理台：<http://127.0.0.1:8000>
- API 文档：<http://127.0.0.1:8000/api/docs>
- 健康检查：<http://127.0.0.1:8000/api/v1/health>

管理台按钮与命令对应关系：

| 管理台操作 | 对应命令 |
|---|---|
| 导入名单 | `python run.py import-list` |
| 解析账号 | `python run.py resolve` |
| 增量历史 | `python run.py history` |
| 抓取正文 | `python run.py content` |
| 归档链接 | 新增公开文章并创建正文任务 |

管理台显示会话数量；会话 API 也只返回名称等元数据，不会返回 Cookie。

## 10. 配置说明

主要配置位于 `config.yaml`：

```yaml
paths:
  name_list: ../name_list.xlsx
  database: data/wechat_archive.db
  sessions_dir: sessions

crawl:
  start_date: "2018-01-01"
  end_date: "2026-12-31"
  sleep_min: 4
  sleep_max: 9
  history_page_size: 10
  content_max_retries: 3

http:
  timeout: 30
  max_retries: 3
  backoff_factor: 1.0
  trust_env: false
  proxy:

jobs:
  workers: 1
```

字段说明：

| 字段 | 说明 | 建议 |
|---|---|---|
| `start_date` / `end_date` | 研究所需文章时间范围 | 根据论文范围修改 |
| `sleep_min` / `sleep_max` | 请求之间的随机等待秒数 | 不要随意降低 |
| `history_page_size` | 历史列表单页数量 | 保持 10 |
| `content_max_retries` | 正文失败最大尝试次数 | 2–3 |
| `timeout` | 单次 HTTP 超时秒数 | 30–60 |
| `max_retries` | HTTP 层重试次数 | 2–3 |
| `proxy` | 可选的抓取代理地址 | 不需要时留空 |
| `workers` | 持久任务 worker 数 | 历史抓取建议保持 1 |

`trust_env: false` 表示爬虫运行时不自动使用系统代理。这与 Charles 抓包是两个阶段：

- 获取会话时：手机通过 Charles。
- 正式爬取时：Python 默认直接联网，不通过 Charles。

## 11. 常用命令

| 命令 | 作用 |
|---|---|
| `python run.py init-db` | 初始化或迁移数据库 |
| `python run.py import-list` | 导入配置中的 Excel 名单 |
| `python run.py import-list --file 文件.xlsx` | 导入指定 Excel |
| `python run.py resolve --limit 3` | 最多解析 3 个账号 |
| `python run.py history --limit 1` | 最多拉取 1 个账号的历史 |
| `python run.py content --limit 20` | 最多抓取 20 篇正文 |
| `python run.py retry-failed --limit 20` | 将失败正文重新放回队列 |
| `python run.py status` | 查看账号、文章和会话状态 |
| `python run.py export-jsonl` | 导出成功文章 |
| `python run.py serve` | 启动 API 和管理台 |
| `python run.py pilot --limit 3` | 执行小规模端到端试点 |

所有命令都可指定其他配置文件：

```bash
python run.py --config config.yaml status
```

## 12. 状态含义

账号历史列表状态：

| 状态 | 含义 |
|---|---|
| `pending` | 尚未拉取 |
| `running` | 正在处理 |
| `done` | 本轮历史列表处理完成 |
| `failed` | 网络、解析或其他错误，可重试 |
| `need_session` | 会话过期、验证或接口拒绝，需要更新会话 |

文章状态：

| 状态 | 含义 |
|---|---|
| `listed` | 已获得链接，等待正文 |
| `retry_wait` | 暂时失败，等待退避重试 |
| `ok` | 正文归档成功 |
| `deleted` | 页面显示已删除或不可访问 |
| `failed` | 已耗尽重试次数 |
| `out_of_range` | 不在配置的研究时间范围 |

## 13. 常见问题

### 可用会话数一直是 0

检查会话文件位置、文件名、`enabled`、三个参数和 YAML 格式。`example_session.yaml` 会被程序主动忽略。

### `invalid session`、`ret=-3` 或 `need_session`

通常表示 `key` 或 Cookie 已过期。重新执行 Charles 抓包步骤，更新原会话文件，然后：

```bash
python run.py history --limit 1
```

### 返回“访问过于频繁”或要求验证身份

立即停止任务并等待。不要增加会话数量、代理数量或并发来绕过验证。确认微信客户端可以正常访问后，再用小范围任务测试。必要时增大：

```yaml
crawl:
  sleep_min: 8
  sleep_max: 15
```

### `history non-json response`

历史接口返回了 HTML，常见原因是会话失效、验证页面或接口发生变化。先在微信客户端检查访问是否正常，再重新获取会话。

### 文章一直处于 `retry_wait`

程序会等待 `next_retry_at` 后再尝试。若需要重新处理已失败文章：

```bash
python run.py retry-failed --limit 20
python run.py content --limit 20
```

### Excel 提示缺少列

首行必须包含：

```text
nickname
link
```

其中链接列也可写成 `sample_url` 或 `url`。列名不区分大小写，但不要添加多余空格或合并单元格。

### 管理台打不开

1. 确认已经运行 `npm run build`。
2. 确认 `web_ui/dist/` 存在。
3. 确认 `python run.py serve` 没有报错。
4. 使用 <http://127.0.0.1:8000>，不要直接双击 `index.html`。

### 端口 8000 被占用

```bash
python run.py serve --port 8001
```

然后访问 <http://127.0.0.1:8001>。

## 14. 数据、导出与备份

主要数据：

```text
data/wechat_archive.db
```

建议在停止程序后备份：

```bash
cp data/wechat_archive.db "data/wechat_archive-$(date +%Y%m%d).db"
```

导出：

```bash
python run.py export-jsonl
```

默认输出：

```text
export/articles.jsonl
```

用 Python/pandas 分析：

```python
import sqlite3
import pandas as pd

conn = sqlite3.connect("data/wechat_archive.db")
df = pd.read_sql_query(
    """
    SELECT a.account_name, ar.title, ar.publish_time,
           ar.content_text, ar.url
    FROM articles ar
    JOIN accounts a ON a.id = ar.account_id
    WHERE ar.status = 'ok'
    """,
    conn,
)
print(df.head())
```

## 15. Docker

确认 `../name_list.xlsx` 存在，然后：

```bash
docker compose up --build
```

访问 <http://127.0.0.1:8000>。

Compose 会挂载：

- `./data` → 数据库
- `./sessions` → 会话文件
- `../name_list.xlsx` → Excel 名单

修改本地 `sessions/session_1.yaml` 后，容器可以读取新会话。会话文件仍然不得上传到仓库。

## 16. 开发与测试

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
pytest

cd web_ui
npm install
npm run build
npm audit
```

## 17. 安全与合规

- 仅归档你有权访问和研究的内容。
- 遵守微信平台规则、著作权、个人信息保护和所在机构的研究伦理要求。
- 不要把管理台直接监听到公网；默认 `127.0.0.1` 只允许本机访问。
- 不要提交 `sessions/session_*.yaml`、数据库或导出文件。
- 不要在日志、论文附录、截图和共享文档中公开 `uin`、`key` 或 Cookie。
- 遇到验证码、环境异常或明确封禁时停止任务并人工检查。
- 历史列表保持单 worker，优先保护账号安全和数据质量。
- Charles 根证书只在必要时安装，抓包结束后关闭代理并取消信任或删除证书。
