import { FormEvent, useCallback, useEffect, useState } from "react";

type Stats = {
  accounts: { resolve_status: string; list_status: string; count: number }[];
  articles: Record<string, number>;
  jobs: Record<string, number>;
  sessions?: number;
  platform_configured?: boolean;
  platform_backend?: string;
  errors?: {
    list_by_kind?: Record<string, number>;
    resolve_by_kind?: Record<string, number>;
    content_by_kind?: Record<string, number>;
    list_errors?: { error: string; count: number }[];
  };
  next_actions?: string[];
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
  heartbeat_at?: string;
  progress_current: number;
  progress_total?: number;
  result?: Record<string, unknown>;
  error?: string;
};
type Cursor = { before_ts: number; before_id: number };

const PAGE_SIZE = 50;
const STATUS_OPTIONS = ["", "ok", "listed", "retry_wait", "failed", "deleted", "out_of_range"];
const STATUS_LABELS: Record<string, string> = {
  ok: "已归档",
  listed: "待抓取",
  retry_wait: "等待重试",
  failed: "失败",
  deleted: "已删除",
  out_of_range: "超出范围",
  pending: "等待中",
  running: "运行中",
  done: "完成",
  cancelled: "已取消",
};
const STAGE_LABELS: Record<string, string> = {
  import: "导入名单",
  resolve: "解析账号",
  history: "增量历史",
  content: "抓取正文",
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
  const [articleTotal, setArticleTotal] = useState(0);
  const [jobs, setJobs] = useState<Job[]>([]);
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [cursor, setCursor] = useState<Cursor | null>(null);
  const [cursorHistory, setCursorHistory] = useState<(Cursor | null)[]>([]);
  const [nextCursor, setNextCursor] = useState<Cursor | null>(null);
  const [publicUrl, setPublicUrl] = useState("");
  const [preview, setPreview] = useState<number | null>(null);
  const [error, setError] = useState("");
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const params = new URLSearchParams({
        limit: String(PAGE_SIZE),
      });
      if (debouncedQuery) params.set("q", debouncedQuery);
      if (statusFilter) params.set("status", statusFilter);
      if (cursor) {
        params.set("before_ts", String(cursor.before_ts));
        params.set("before_id", String(cursor.before_id));
      }
      const [nextStats, articlePage, nextJobs] = await Promise.all([
        api<Stats>("/api/v1/stats"),
        api<{ items: Article[]; total: number; next_cursor: Cursor | null }>(
          `/api/v1/articles?${params}`,
        ),
        api<Job[]>("/api/v1/jobs"),
      ]);
      setStats(nextStats);
      setArticles(articlePage.items);
      setArticleTotal(articlePage.total);
      setNextCursor(articlePage.next_cursor);
      setJobs(nextJobs);
      setError("");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "加载失败");
    }
  }, [cursor, debouncedQuery, statusFilter]);

  useEffect(() => {
    const timer = window.setTimeout(() => {
      setDebouncedQuery(query.trim());
      setCursor(null);
      setCursorHistory([]);
    }, 400);
    return () => window.clearTimeout(timer);
  }, [query]);

  useEffect(() => {
    setCursor(null);
    setCursorHistory([]);
  }, [statusFilter]);

  const hasActiveJobs = jobs.some((job) => job.status === "pending" || job.status === "running");
  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), hasActiveJobs ? 3000 : 15000);
    return () => window.clearInterval(timer);
  }, [hasActiveJobs, refresh]);

  const run = async (stage: string, extra: Record<string, unknown> = {}) => {
    setPendingAction(stage);
    setError("");
    try {
      await api("/api/v1/jobs", {
        method: "POST",
        body: JSON.stringify({ stage, ...extra }),
      });
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "任务提交失败");
    } finally {
      setPendingAction(null);
    }
  };

  const retryResolve = async () => {
    setPendingAction("retry-resolve");
    setError("");
    try {
      await api("/api/v1/retry-resolve", { method: "POST", body: "{}" });
      await run("resolve");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重试解析失败");
      setPendingAction(null);
    }
  };

  const retryContent = async () => {
    setPendingAction("retry-content");
    setError("");
    try {
      // reuse job path: content will pick listed + retry_wait; failed need CLI retry-failed
      await api("/api/v1/jobs", {
        method: "POST",
        body: JSON.stringify({ stage: "content" }),
      });
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "重试正文失败");
    } finally {
      setPendingAction(null);
    }
  };

  const addUrl = async (event: FormEvent) => {
    event.preventDefault();
    setPendingAction("public");
    setError("");
    try {
      await api("/api/v1/articles/public", {
        method: "POST",
        body: JSON.stringify({ url: publicUrl }),
      });
      await api("/api/v1/jobs", {
        method: "POST",
        body: JSON.stringify({ stage: "content" }),
      });
      setPublicUrl("");
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "归档链接失败");
    } finally {
      setPendingAction(null);
    }
  };

  const manualRefresh = async () => {
    setRefreshing(true);
    try {
      await refresh();
    } finally {
      setRefreshing(false);
    }
  };

  const cancelJob = async (jobId: number) => {
    setError("");
    try {
      await api(`/api/v1/jobs/${jobId}/cancel`, { method: "POST" });
      await refresh();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "取消任务失败");
    }
  };

  const nextPage = () => {
    if (!nextCursor) return;
    setCursorHistory((history) => [...history, cursor]);
    setCursor(nextCursor);
  };

  const previousPage = () => {
    setCursorHistory((history) => {
      if (!history.length) return history;
      setCursor(history[history.length - 1]);
      return history.slice(0, -1);
    });
  };

  const page = cursorHistory.length + 1;
  const totalPages = Math.max(1, Math.ceil(articleTotal / PAGE_SIZE));
  const hasRunningAction = pendingAction !== null;
  const activeStages = new Set(
    jobs
      .filter((job) => job.status === "pending" || job.status === "running")
      .map((job) => job.stage),
  );

  return (
    <main>
      <header>
        <div>
          <p className="eyebrow">LOCAL RESEARCH ARCHIVE</p>
          <h1>微信文章归档</h1>
          <p className="subtle">可恢复采集、质量状态与本地全文浏览</p>
        </div>
        <button disabled={refreshing} onClick={() => void manualRefresh()}>
          {refreshing ? "刷新中…" : "刷新"}
        </button>
      </header>

      {error && <aside className="error">{error}</aside>}

      <section className="metrics">
        <Metric label="文章总数" value={sum(stats?.articles)} />
        <Metric label="归档成功" value={stats?.articles.ok ?? 0} />
        <Metric label="等待处理" value={(stats?.articles.listed ?? 0) + (stats?.articles.retry_wait ?? 0)} />
        <Metric
          label="平台凭证"
          value={stats?.platform_configured ? "已配置" : "未配置"}
        />
      </section>

      {(stats?.next_actions?.length || stats?.errors?.list_by_kind) && (
        <section className="panel">
          <h2>运维诊断</h2>
          {stats?.errors?.list_by_kind && (
            <p className="subtle">
              历史错误：
              {Object.entries(stats.errors.list_by_kind)
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ") || "无"}
              {stats.errors.resolve_by_kind
                ? ` ｜ 解析：${Object.entries(stats.errors.resolve_by_kind)
                    .map(([k, v]) => `${k}=${v}`)
                    .join(" · ")}`
                : ""}
            </p>
          )}
          <ul className="subtle" style={{ margin: "0.5rem 0 0", paddingLeft: "1.2rem" }}>
            {(stats?.next_actions ?? []).slice(0, 5).map((action) => (
              <li key={action}>{action}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="panel actions">
        <div>
          <h2>采集任务</h2>
          <p className="subtle">历史默认跳过已完成账号；频控会熔断暂停。正文失败会自动退避重试。</p>
        </div>
        <div className="buttonRow">
          {["import", "resolve", "history", "content"].map((stage) => (
            <button
              className={stage === "content" ? "primary" : undefined}
              disabled={hasRunningAction || activeStages.has(stage)}
              key={stage}
              onClick={() => void run(stage)}
            >
              {pendingAction === stage ? "提交中…" : STAGE_LABELS[stage]}
            </button>
          ))}
          <button
            disabled={hasRunningAction || activeStages.has("history")}
            onClick={() => void run("history", { refresh: true })}
          >
            {pendingAction === "history" ? "提交中…" : "历史(含done刷新)"}
          </button>
          <button disabled={hasRunningAction} onClick={() => void retryResolve()}>
            {pendingAction === "retry-resolve" || pendingAction === "resolve"
              ? "处理中…"
              : "重试解析失败"}
          </button>
          <button disabled={hasRunningAction} onClick={() => void retryContent()}>
            {pendingAction === "retry-content" || pendingAction === "content"
              ? "处理中…"
              : "继续抓正文"}
          </button>
        </div>
        <form onSubmit={(event) => void addUrl(event)}>
          <input
            type="url"
            required
            placeholder="粘贴公开的 mp.weixin.qq.com 文章链接"
            value={publicUrl}
            onChange={(event) => setPublicUrl(event.target.value)}
          />
          <button disabled={hasRunningAction} type="submit">
            {pendingAction === "public" ? "归档中…" : "归档链接"}
          </button>
        </form>
      </section>

      <div className="columns">
        <section className="panel">
          <div className="sectionHead">
            <h2>文章库</h2>
            <div className="filters">
              <select
                aria-label="按状态筛选"
                value={statusFilter}
                onChange={(event) => setStatusFilter(event.target.value)}
              >
                {STATUS_OPTIONS.map((status) => (
                  <option key={status || "all"} value={status}>
                    {status ? STATUS_LABELS[status] || status : "全部状态"}
                  </option>
                ))}
              </select>
              <input
                aria-label="搜索文章"
                placeholder="标题、作者或正文"
                value={query}
                onChange={(event) => setQuery(event.target.value)}
              />
            </div>
          </div>
          <p className="subtle listSummary">共 {articleTotal.toLocaleString()} 篇，当前第 {page} / {totalPages} 页</p>
          <div className="list">
            {articles.map((article) => (
              <button className="row" key={article.id} onClick={() => setPreview(article.id)}>
                <span>
                  <strong>{article.title || "等待抓取标题"}</strong>
                  <small>{article.account_name || "未知公众号"} · {article.publish_time || "时间未知"}</small>
                  {article.content_error && (
                    <small className="inlineError">失败原因：{article.content_error}</small>
                  )}
                </span>
                <Status value={article.status} />
              </button>
            ))}
            {!articles.length && <p className="empty">暂无匹配文章</p>}
          </div>
          <div className="pager">
            <button disabled={!cursorHistory.length} onClick={previousPage}>
              上一页
            </button>
            <button disabled={!nextCursor} onClick={nextPage}>
              下一页
            </button>
          </div>
        </section>

        <section className="panel jobs">
          <h2>任务中心</h2>
          <div className="list">
            {jobs.slice(0, 12).map((job) => (
              <div className="row" key={job.id}>
                <span>
                  <strong>#{job.id} {STAGE_LABELS[job.stage] || job.stage}</strong>
                  <small>
                    {job.created_at}
                    {job.progress_total != null
                      ? ` · ${job.progress_current.toLocaleString()} / ${job.progress_total.toLocaleString()}`
                      : ""}
                    {job.error ? ` · ${job.error}` : ""}
                  </small>
                  {job.progress_total != null && job.progress_total > 0 && (
                    <progress value={job.progress_current} max={job.progress_total} />
                  )}
                </span>
                <span className="jobControls">
                  <Status value={job.status} />
                  {(job.status === "pending" || job.status === "running") && (
                    <button className="smallButton" onClick={() => void cancelJob(job.id)}>
                      取消
                    </button>
                  )}
                </span>
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

function Metric({ label, value }: { label: string; value: number | string }) {
  return <article><span>{label}</span><strong>{value.toLocaleString()}</strong></article>;
}

function Status({ value }: { value: string }) {
  return <span className={`status status-${value}`}>{STATUS_LABELS[value] || value}</span>;
}

function sum(values?: Record<string, number>) {
  return Object.values(values ?? {}).reduce((total, value) => total + value, 0);
}
