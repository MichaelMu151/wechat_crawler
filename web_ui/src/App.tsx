import { FormEvent, useCallback, useEffect, useState } from "react";

type Stats = {
  accounts: { resolve_status: string; list_status: string; count: number }[];
  articles: Record<string, number>;
  jobs: Record<string, number>;
  sessions: number;
};
type Article = {
  id: number;
  title?: string;
  account_name?: string;
  publish_time?: string;
  status: string;
  content_error?: string;
};
type Job = {
  id: number;
  stage: string;
  status: string;
  created_at: string;
  error?: string;
};

const api = async <T,>(path: string, init?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
};

export default function App() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [articles, setArticles] = useState<Article[]>([]);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [query, setQuery] = useState("");
  const [publicUrl, setPublicUrl] = useState("");
  const [preview, setPreview] = useState<number | null>(null);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try {
      const search = query ? `?q=${encodeURIComponent(query)}` : "";
      const [nextStats, articlePage, nextJobs] = await Promise.all([
        api<Stats>("/api/v1/stats"),
        api<{ items: Article[] }>(`/api/v1/articles${search}`),
        api<Job[]>("/api/v1/jobs"),
      ]);
      setStats(nextStats);
      setArticles(articlePage.items);
      setJobs(nextJobs);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载失败");
    }
  }, [query]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const run = async (stage: string) => {
    await api("/api/v1/jobs", {
      method: "POST",
      body: JSON.stringify({ stage }),
    });
    await refresh();
  };

  const addUrl = async (event: FormEvent) => {
    event.preventDefault();
    await api("/api/v1/articles/public", {
      method: "POST",
      body: JSON.stringify({ url: publicUrl }),
    });
    setPublicUrl("");
    await run("content");
  };

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">LOCAL RESEARCH ARCHIVE</p>
          <h1>微信文章归档</h1>
          <p className="subtle">可恢复采集、质量状态与本地全文浏览</p>
        </div>
        <button onClick={() => void refresh()}>刷新</button>
      </header>

      {error && <aside className="error">{error}</aside>}

      <section className="metrics">
        <Metric label="文章总数" value={sum(stats?.articles)} />
        <Metric label="归档成功" value={stats?.articles.ok ?? 0} />
        <Metric label="等待处理" value={(stats?.articles.listed ?? 0) + (stats?.articles.retry_wait ?? 0)} />
        <Metric label="有效会话" value={stats?.sessions ?? 0} />
      </section>

      <section className="panel actions">
        <div>
          <h2>采集任务</h2>
          <p className="subtle">历史列表保持低并发，正文失败会自动退避重试。</p>
        </div>
        <div className="buttonRow">
          <button onClick={() => void run("import")}>导入名单</button>
          <button onClick={() => void run("resolve")}>解析账号</button>
          <button onClick={() => void run("history")}>增量历史</button>
          <button className="primary" onClick={() => void run("content")}>抓取正文</button>
        </div>
        <form onSubmit={(event) => void addUrl(event)}>
          <input
            type="url"
            required
            placeholder="粘贴公开的 mp.weixin.qq.com 文章链接"
            value={publicUrl}
            onChange={(event) => setPublicUrl(event.target.value)}
          />
          <button type="submit">归档链接</button>
        </form>
      </section>

      <div className="columns">
        <section className="panel">
          <div className="sectionHead">
            <h2>文章库</h2>
            <input
              aria-label="搜索文章"
              placeholder="标题、作者或正文"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
            />
          </div>
          <div className="list">
            {articles.map((article) => (
              <button className="row" key={article.id} onClick={() => setPreview(article.id)}>
                <span>
                  <strong>{article.title || "等待抓取标题"}</strong>
                  <small>{article.account_name || "未知公众号"} · {article.publish_time || "时间未知"}</small>
                </span>
                <Status value={article.status} />
              </button>
            ))}
            {!articles.length && <p className="empty">暂无匹配文章</p>}
          </div>
        </section>

        <section className="panel jobs">
          <h2>任务中心</h2>
          <div className="list">
            {jobs.slice(0, 12).map((job) => (
              <div className="row" key={job.id}>
                <span>
                  <strong>#{job.id} {job.stage}</strong>
                  <small>{job.created_at}{job.error ? ` · ${job.error}` : ""}</small>
                </span>
                <Status value={job.status} />
              </div>
            ))}
            {!jobs.length && <p className="empty">尚未创建任务</p>}
          </div>
        </section>
      </div>

      {preview !== null && (
        <div className="modal" role="dialog" aria-modal="true">
          <div className="modalCard">
            <button className="close" onClick={() => setPreview(null)}>关闭</button>
            <iframe
              title="文章预览"
              sandbox=""
              src={`/api/v1/articles/${preview}/preview`}
            />
          </div>
        </div>
      )}
    </main>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return <article><span>{label}</span><strong>{value.toLocaleString()}</strong></article>;
}

function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}>{value}</span>;
}

function sum(values?: Record<string, number>) {
  return Object.values(values ?? {}).reduce((total, value) => total + value, 0);
}
