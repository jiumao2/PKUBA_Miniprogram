import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type CSSProperties,
} from "react";
import {
  ApiError,
  type AdminSeason,
  type ArchiveJob,
  type ArchiveKind,
  type MediaPurgeJob,
  type MediaPurgePreview,
  type StorageSummary,
  createAdminClient,
} from "@pkuba/api-client";

import { useAdminDirtySource } from "./dirtyGuard";
import { formatAdminSeasonLabel } from "./seasonLabel";
import "./archive-management.css";

type AdminClient = ReturnType<typeof createAdminClient>;

const kindLabels: Record<ArchiveKind, string> = {
  SEASON_DATA: "赛季数据包",
  SEASON_PHOTOS: "赛季照片包",
  SYSTEM_RAW: "全系统原始备份",
};

const statusLabels: Record<string, string> = {
  QUEUED: "等待生成",
  BUILDING: "正在生成",
  READY: "可以下载",
  FAILED: "生成失败",
  EXPIRED: "已过期",
  DISCARDED: "已从服务器清理",
  COMPLETED: "清理完成",
  COMPLETED_WITH_WARNINGS: "完成但有警告",
};

const formatBytes = (value: number) => {
  if (value < 1024) return `${value} B`;
  const units = ["KiB", "MiB", "GiB", "TiB"];
  let size = value / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && size >= 1024; index += 1) {
    size /= 1024;
    unit = units[index];
  }
  return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${unit}`;
};

const formatDateTime = (value: string | null) =>
  value ? new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(new Date(value)) : "—";

export function ArchiveManagementPage({
  client,
  seasons,
  seasonId,
  onSeasonChange,
}: {
  client: AdminClient;
  seasons: AdminSeason[];
  seasonId: string;
  onSeasonChange: (seasonId: string) => void;
}) {
  const [storage, setStorage] = useState<StorageSummary | null>(null);
  const [seasonJobs, setSeasonJobs] = useState<ArchiveJob[]>([]);
  const [systemJobs, setSystemJobs] = useState<ArchiveJob[]>([]);
  const [purgeJobs, setPurgeJobs] = useState<MediaPurgeJob[]>([]);
  const [purgePreview, setPurgePreview] = useState<MediaPurgePreview | null>(null);
  const [currentPassword, setCurrentPassword] = useState("");
  const [confirmedPreviewHash, setConfirmedPreviewHash] = useState<string | null>(null);
  const [busy, setBusy] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const loadGenerationRef = useRef(0);

  const season = seasons.find((item) => item.id === seasonId) ?? seasons[0];
  const hasActiveJob =
    [...seasonJobs, ...systemJobs].some((job) => ["QUEUED", "BUILDING"].includes(job.status))
    || purgeJobs.some((job) => ["QUEUED", "BUILDING"].includes(job.status));
  const externalCopyConfirmed = Boolean(
    purgePreview && confirmedPreviewHash === purgePreview.preview_hash,
  );

  useAdminDirtySource(
    "archive-management-form",
    Boolean(currentPassword || confirmedPreviewHash),
  );

  const load = useCallback(async () => {
    if (!season) return;
    const generation = ++loadGenerationRef.current;
    try {
      const [nextStorage, nextSeasonJobs, nextSystemJobs, nextPurgeJobs, nextPurgePreview] = await Promise.all([
        client.getArchiveStorageSummary(),
        client.listSeasonExports(season.id),
        client.listSystemBackups(),
        client.listMediaPurgeJobs(season.id),
        season.status === "ARCHIVED"
          ? client.previewMediaPurge(season.id)
          : Promise.resolve(null),
      ]);
      if (loadGenerationRef.current !== generation) return;
      setStorage(nextStorage);
      setSeasonJobs(nextSeasonJobs.items);
      setSystemJobs(nextSystemJobs.items);
      setPurgeJobs(nextPurgeJobs.items);
      setPurgePreview(nextPurgePreview);
      setConfirmedPreviewHash((current) =>
        current && current === nextPurgePreview?.preview_hash ? current : null,
      );
    } catch (reason: unknown) {
      if (loadGenerationRef.current !== generation) return;
      setError(reason instanceof Error ? reason.message : "无法读取备份状态");
    }
  }, [client, season?.id, season?.status]);

  useEffect(() => {
    loadGenerationRef.current += 1;
    setConfirmedPreviewHash(null);
    setPurgePreview(null);
    setSeasonJobs([]);
    setPurgeJobs([]);
    setNotice(null);
    setError(null);
  }, [season?.id]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    if (!hasActiveJob) return;
    const timer = window.setInterval(() => void load(), 3000);
    return () => window.clearInterval(timer);
  }, [hasActiveJob, load]);

  const selectedStorage = useMemo(
    () => storage?.seasons.find((item) => item.season_id === season?.id),
    [season?.id, storage],
  );

  const run = async (key: string, operation: () => Promise<void>) => {
    setBusy(key);
    setError(null);
    setNotice(null);
    try {
      await operation();
      await load();
    } catch (reason: unknown) {
      setError(
        reason instanceof ApiError || reason instanceof Error
          ? reason.message
          : "操作失败，请刷新后重试",
      );
    } finally {
      setBusy("");
    }
  };

  const createSeasonPackage = async (kind: "SEASON_DATA" | "SEASON_PHOTOS") => {
    if (!season) return;
    await run(kind, async () => {
      const preview = await client.previewSeasonExport(season.id, kind, season.version);
      if (!preview.ready) throw new Error(preview.blockers[0]?.message ?? "当前不能生成归档");
      if (!window.confirm(`预计需要 ${formatBytes(preview.estimated_bytes)} 临时空间，是否开始生成${kindLabels[kind]}？`)) return;
      await client.createSeasonExport(season.id, kind, season.version);
      setNotice("任务已经进入后台队列，页面会自动刷新进度。");
    });
  };

  const createSystemBackup = async () => {
    if (!currentPassword) {
      setError("请输入当前网页登录密码。");
      return;
    }
    await run("SYSTEM_RAW", async () => {
      const preview = await client.previewSystemBackup();
      if (!preview.ready) throw new Error(preview.blockers[0]?.message ?? "当前不能生成备份");
      if (!window.confirm("该文件包含全部 OpenID、密码哈希和私有照片，且不会加密。确认在 HTTPS 或本机环境生成？")) return;
      await client.createSystemBackup(currentPassword);
      setCurrentPassword("");
      setNotice("全系统备份正在后台生成。系统将使用一致性快照；仍建议在业务低峰操作。");
    });
  };

  const download = async (job: ArchiveJob) => {
    await run(`download-${job.id}`, async () => {
      const ticket = await client.issueArchiveDownloadTicket(job.id);
      window.location.assign(ticket.url);
      setNotice(`已开始下载 ${ticket.filename}。请按页面显示的 SHA-256 核对文件。`);
    });
  };

  const confirmSaved = async (job: ArchiveJob) => {
    if (!window.confirm("确认文件已保存到服务器以外的位置？确认后服务器上的临时包会立即删除。")) return;
    await run(`confirm-${job.id}`, async () => {
      await client.confirmArchiveSaved(job.id, job.version);
      setNotice("外部保存已确认，服务器临时包已经清理。");
    });
  };

  const applyPurge = async () => {
    if (!season || !purgePreview) return;
    if (purgePreview.season_id !== season.id) {
      setConfirmedPreviewHash(null);
      setError("赛季已经切换，请重新读取照片清理预览。");
      return;
    }
    if (!externalCopyConfirmed) {
      setError("请先确认数据包和照片包已经保存到服务器外。");
      return;
    }
    if (!window.confirm(`将永久删除 ${purgePreview.files} 个照片文件（${formatBytes(purgePreview.bytes)}）。数据库记录会保留，但照片无法在线恢复。是否继续？`)) return;
    await run("purge", async () => {
      await client.applyMediaPurge(season.id, {
        preview_hash: purgePreview.preview_hash,
        expected_season_version: season.version,
        confirmed_external_copy: true,
        confirm_permanent_delete: true,
      });
      setConfirmedPreviewHash(null);
      setNotice("照片清理任务已经提交，页面会自动刷新状态。");
    });
  };

  const retryPurge = async (job: MediaPurgeJob) => {
    await run(`retry-purge-${job.id}`, async () => {
      await client.retryMediaPurge(job.id, job.version);
      setNotice("照片清理任务已恢复，只会继续处理尚未完成的文件。");
    });
  };

  if (!season) return <section className="state-panel"><h2>暂无赛季</h2><p>创建赛季后即可使用备份与归档。</p></section>;

  return (
    <div className="archive-page">
      <section className="archive-heading">
        <div>
          <p className="eyebrow">本地私有存储</p>
          <h2>备份与归档</h2>
          <p>结构化数据长期保留；照片归档到管理员设备后，可从已结束赛季释放服务器空间。</p>
        </div>
        <label>
          当前赛季
          <select value={season.id} onChange={(event) => onSeasonChange(event.target.value)}>
            {seasons.map((item) => <option key={item.id} value={item.id}>{formatAdminSeasonLabel(item)}</option>)}
          </select>
        </label>
      </section>

      {notice && <div className="archive-notice" role="status">{notice}</div>}
      {error && <div className="archive-notice error" role="alert">{error}</div>}

      {storage && (
        <section className="archive-storage">
          <div className="storage-gauge" style={{ "--used": `${Math.min(100, (storage.disk_used_bytes / storage.disk_total_bytes) * 100)}%` } as CSSProperties}>
            <div><span>服务器磁盘</span><strong>{formatBytes(storage.disk_free_bytes)} 可用</strong></div>
            <div className="storage-track"><i /></div>
            <small>已用 {formatBytes(storage.disk_used_bytes)} / {formatBytes(storage.disk_total_bytes)} · 安全预留 {formatBytes(storage.reserve_bytes)}</small>
          </div>
          <div className="storage-metrics">
            <Metric label="数据库" value={formatBytes(storage.database_bytes)} />
            <Metric label="在线照片" value={formatBytes(storage.online_media_bytes)} />
            <Metric label="临时归档" value={formatBytes(storage.staged_artifact_bytes)} />
            <Metric label={`${season.name} 照片`} value={formatBytes(selectedStorage?.online_bytes ?? 0)} />
          </div>
          {selectedStorage && (
            <div className="storage-breakdown">
              <span>记录表 {formatBytes(selectedStorage.scoresheet_bytes)}</span>
              <span>比赛合照 {formatBytes(selectedStorage.group_photo_bytes)}</span>
              <span>其他照片 {formatBytes(selectedStorage.game_photo_bytes)}</span>
              <span>{selectedStorage.online_files} 个在线文件</span>
            </div>
          )}
        </section>
      )}

      <section className="archive-actions-grid">
        <article className="archive-action-card">
          <p className="eyebrow">可随时生成</p>
          <h3>赛季结构化数据</h3>
          <p>XLSX 用于查阅，JSONL 用于无损恢复；不包含 OpenID、密码或照片原文件。</p>
          <button className="primary-action" disabled={Boolean(busy)} onClick={() => void createSeasonPackage("SEASON_DATA")}>
            {busy === "SEASON_DATA" ? "正在提交…" : "生成数据包"}
          </button>
        </article>
        <article className="archive-action-card">
          <p className="eyebrow">扁平文件夹</p>
          <h3>赛季全部照片</h3>
          <p>记录表、比赛合照、其他照片及历史版本统一按比赛日期与对阵命名。</p>
          <button className="primary-action" disabled={Boolean(busy) || Boolean(selectedStorage && selectedStorage.online_files === 0)} onClick={() => void createSeasonPackage("SEASON_PHOTOS")}>
            {busy === "SEASON_PHOTOS" ? "正在提交…" : "生成照片包"}
          </button>
        </article>
        <article className="archive-action-card sensitive">
          <p className="eyebrow">敏感 · 不加密</p>
          <h3>全系统原始备份</h3>
          <p>包含数据库原始转储和全部私有照片，只能由核心开发者在 HTTPS 或本机下载。</p>
          <input type="password" autoComplete="current-password" placeholder="当前网页登录密码" value={currentPassword} onChange={(event) => setCurrentPassword(event.target.value)} />
          <button className="danger-action" disabled={Boolean(busy)} onClick={() => void createSystemBackup()}>
            {busy === "SYSTEM_RAW" ? "正在提交…" : "生成全系统备份"}
          </button>
        </article>
      </section>

      <JobTable title={`${season.year} ${season.name} 导出记录`} jobs={seasonJobs} busy={busy} onDownload={download} onConfirm={confirmSaved} />
      <JobTable title="全系统备份记录" jobs={systemJobs} busy={busy} onDownload={download} onConfirm={confirmSaved} />

      {season.status === "ARCHIVED" && purgePreview && (
        <section className="purge-panel">
          <div>
            <p className="eyebrow">不可逆操作</p>
            <h3>释放已归档赛季照片</h3>
            <p>预计删除 <strong>{purgePreview.files}</strong> 个文件，释放 <strong>{formatBytes(purgePreview.bytes)}</strong>。结构化记录、统计、哈希和审计永久保留。</p>
          </div>
          {purgePreview.blockers.length > 0 && (
            <ul>{purgePreview.blockers.map((blocker) => <li key={blocker.code}>{blocker.message}</li>)}</ul>
          )}
          <label className="archive-confirm-check">
            <input
              type="checkbox"
              checked={externalCopyConfirmed}
              onChange={(event) => setConfirmedPreviewHash(
                event.target.checked ? purgePreview.preview_hash : null,
              )}
            />
            我已将最终数据包和照片包保存到服务器以外的位置
          </label>
          <button className="danger-action" disabled={!purgePreview.ready || !externalCopyConfirmed || Boolean(busy)} onClick={() => void applyPurge()}>
            {busy === "purge" ? "正在提交…" : "永久删除服务器照片"}
          </button>
        </section>
      )}
      {purgeJobs.length > 0 && (
        <PurgeHistory
          jobs={purgeJobs}
          busy={busy}
          onRetry={retryPurge}
        />
      )}
    </div>
  );
}

function JobTable({ title, jobs, busy, onDownload, onConfirm }: {
  title: string;
  jobs: ArchiveJob[];
  busy: string;
  onDownload: (job: ArchiveJob) => Promise<void>;
  onConfirm: (job: ArchiveJob) => Promise<void>;
}) {
  return (
    <section className="archive-history">
      <div className="section-heading"><h3>{title}</h3><span>{jobs.length} 项</span></div>
      {jobs.length === 0 ? <p className="archive-empty">尚未生成归档。</p> : (
        <div className="archive-table-wrap"><table><thead><tr><th>类型与时间</th><th>状态</th><th>大小与校验</th><th>操作</th></tr></thead><tbody>
          {jobs.map((job) => <tr key={job.id}>
            <td><strong>{kindLabels[job.kind]}</strong><span>{formatDateTime(job.created_at)}</span>{job.is_final && <em>最终包</em>}</td>
            <td><span className={`archive-status ${job.status.toLowerCase()}`}>{statusLabels[job.status] ?? job.status}</span>{job.error_message && <small>{job.error_message}</small>}</td>
            <td><strong>{job.byte_size ? formatBytes(job.byte_size) : "—"}</strong><code title={job.file_sha256}>{job.file_sha256 ? job.file_sha256.slice(0, 16) : "等待生成"}</code></td>
            <td><div className="archive-row-actions">
              {job.status === "READY" && <><button disabled={Boolean(busy)} onClick={() => void onDownload(job)}>下载</button><button disabled={Boolean(busy)} onClick={() => void onConfirm(job)}>已保存并清理</button></>}
              {job.download_count > 0 && <small>已发起 {job.download_count} 次下载</small>}
            </div></td>
          </tr>)}
        </tbody></table></div>
      )}
    </section>
  );
}

function PurgeHistory({ jobs, busy, onRetry }: {
  jobs: MediaPurgeJob[];
  busy: string;
  onRetry: (job: MediaPurgeJob) => Promise<void>;
}) {
  return (
    <section className="archive-history">
      <div className="section-heading"><h3>照片清理记录</h3><span>{jobs.length} 项</span></div>
      <div className="archive-table-wrap"><table><thead><tr><th>提交时间</th><th>状态</th><th>处理结果</th><th>操作</th></tr></thead><tbody>
        {jobs.map((job) => <tr key={job.id}>
          <td>{formatDateTime(job.created_at)}</td>
          <td><span className={`archive-status ${job.status.toLowerCase()}`}>{statusLabels[job.status] ?? job.status}</span>{job.error_message && <small>{job.error_message}</small>}</td>
          <td><strong>{job.deleted_files} / {job.expected_files} 个文件</strong><span>已释放 {formatBytes(job.deleted_bytes)}{job.missing_files ? ` · 缺失 ${job.missing_files} 个` : ""}</span></td>
          <td>{job.status === "FAILED" && <button className="archive-retry" disabled={Boolean(busy)} onClick={() => void onRetry(job)}>{busy === `retry-purge-${job.id}` ? "正在恢复…" : "继续清理"}</button>}</td>
        </tr>)}
      </tbody></table></div>
    </section>
  );
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div className="archive-metric"><span>{label}</span><strong>{value}</strong></div>;
}
