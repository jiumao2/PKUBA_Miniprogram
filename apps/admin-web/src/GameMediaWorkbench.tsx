import {
  AlertTriangle,
  Archive,
  CalendarDays,
  ExternalLink,
  FileText,
  Image as ImageIcon,
  MapPin,
  RefreshCw,
  Search,
} from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import type {
  AdminSeason,
  GameMediaAsset,
  ScoresheetQueueItem,
  createAdminClient,
} from "@pkuba/api-client";
import { ApiError, createIdempotencyKey } from "@pkuba/api-client";

import { formatAdminSeasonLabel } from "./seasonLabel";
import "./competition-media.css";

type AdminClient = ReturnType<typeof createAdminClient>;
type ProcessingFilter = "" | "UPLOAD" | "SCORESHEET_REVIEW" | "COMPLETE";

const scoresheetStatusLabels: Record<string, string> = {
  NO_SOURCE: "待上传",
  RECOGNITION_QUEUED: "等待识别",
  RECOGNIZING: "识别中",
  RETRY_WAIT: "等待重试",
  DRAFT: "待人工核对",
  RECOGNITION_FAILED: "识别失败",
  READY: "待发布",
  PUBLISHED: "已发布",
};
const recognitionStatuses = new Set(["RECOGNITION_QUEUED", "RECOGNIZING", "RETRY_WAIT"]);

export function GameMediaWorkbench({
  client,
  seasons,
  seasonId,
  initialGameId = "",
  isSuperadmin = false,
  onSeasonChange,
}: {
  client: AdminClient;
  seasons: AdminSeason[];
  seasonId: string;
  initialGameId?: string;
  isSuperadmin?: boolean;
  onSeasonChange: (seasonId: string) => void;
}) {
  const [games, setGames] = useState<ScoresheetQueueItem[]>([]);
  const [gamesTotal, setGamesTotal] = useState(0);
  const [gamesPage, setGamesPage] = useState(1);
  const [divisionNames, setDivisionNames] = useState<string[]>([]);
  const [assets, setAssets] = useState<GameMediaAsset[]>([]);
  const [gamesLoading, setGamesLoading] = useState(false);
  const [assetsLoading, setAssetsLoading] = useState(false);
  const [gamesError, setGamesError] = useState("");
  const [assetsError, setAssetsError] = useState("");
  const [query, setQuery] = useState("");
  const [division, setDivision] = useState("");
  const [processing, setProcessing] = useState<ProcessingFilter>("");
  const [selectedGameId, setSelectedGameId] = useState("");
  const [selectedAssetId, setSelectedAssetId] = useState("");
  const [replacementFile, setReplacementFile] = useState<File | null>(null);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [retryBatch, setRetryBatch] = useState<{
    gameId: string; kind: "GROUP_PHOTO" | "GAME_PHOTO"; files: File[];
  } | null>(null);
  const gamesRequest = useRef(0);
  const assetsRequest = useRef(0);
  const initialTargetLoaded = useRef(false);
  const mediaOperationKeys = useRef(new Map<string, string>());
  const pageSize = 20;

  const selectedSeason = seasons.find((item) => item.id === seasonId) ?? null;
  const archived = selectedSeason?.status === "ARCHIVED";

  const loadGames = useCallback(async () => {
    const request = ++gamesRequest.current;
    if (!seasonId) {
      setGames([]);
      setGamesLoading(false);
      return;
    }
    setGamesLoading(true);
    setGamesError("");
    try {
      const [result, linked] = await Promise.all([
        client.getScoresheetQueuePage({
          seasonId,
          scope: "ALL",
          query,
          divisionName: division,
          processing,
          page: gamesPage,
          pageSize,
        }),
        initialGameId && !initialTargetLoaded.current
          ? client.getScoresheetQueuePage({ gameId: initialGameId, page: 1, pageSize: 1 })
          : Promise.resolve(null),
      ]);
      if (request === gamesRequest.current) {
        const linkedGame = linked?.items[0];
        setGames(linkedGame && !result.items.some((item) => item.game_id === linkedGame.game_id)
          ? [linkedGame, ...result.items]
          : result.items);
        setGamesTotal(result.total);
        setDivisionNames(result.division_names);
        initialTargetLoaded.current = true;
      }
    } catch (reason: unknown) {
      if (request === gamesRequest.current) {
        setGames([]);
        setGamesTotal(0);
        setGamesError(reason instanceof Error ? reason.message : "无法读取比赛与记录表状态");
      }
    } finally {
      if (request === gamesRequest.current) setGamesLoading(false);
    }
  }, [client, division, gamesPage, initialGameId, processing, query, seasonId]);

  const loadAssets = useCallback(async () => {
    const request = ++assetsRequest.current;
    if (!seasonId) {
      setAssets([]);
      setAssetsLoading(false);
      return;
    }
    setAssetsLoading(true);
    setAssetsError("");
    try {
      const result = await client.listAdminGameMedia({ seasonId });
      if (request === assetsRequest.current) setAssets(result);
    } catch (reason: unknown) {
      if (request === assetsRequest.current) {
        setAssets([]);
        setAssetsError(reason instanceof Error ? reason.message : "无法读取比赛照片");
      }
    } finally {
      if (request === assetsRequest.current) setAssetsLoading(false);
    }
  }, [client, seasonId]);

  useEffect(() => {
    setSelectedGameId("");
    setSelectedAssetId("");
    setQuery("");
    setDivision("");
    setProcessing("");
    setGamesPage(1);
    setGamesTotal(0);
    setDivisionNames([]);
    initialTargetLoaded.current = false;
    setMessage("");
    setRetryBatch(null);
    void loadAssets();
  }, [loadAssets, seasonId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void loadGames(), query ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [loadGames, query]);

  const assetsByGame = useMemo(() => {
    const grouped = new Map<string, GameMediaAsset[]>();
    assets.forEach((asset) => {
      const current = grouped.get(asset.game_id) ?? [];
      current.push(asset);
      grouped.set(asset.game_id, current);
    });
    return grouped;
  }, [assets]);

  const visibleGames = games;

  useEffect(() => {
    setSelectedGameId((current) => {
      if (visibleGames.some((game) => game.game_id === current)) return current;
      return visibleGames.find((game) => game.game_id === initialGameId)?.game_id
        ?? visibleGames[0]?.game_id
        ?? "";
    });
  }, [initialGameId, visibleGames]);

  const selectedGame = games.find((game) => game.game_id === selectedGameId) ?? null;
  const selectedPhotos = useMemo(
    () => (assetsByGame.get(selectedGameId) ?? []).filter((asset) => asset.kind !== "SCORESHEET"),
    [assetsByGame, selectedGameId],
  );

  useEffect(() => {
    setSelectedAssetId((current) => selectedPhotos.some((asset) => asset.id === current)
      ? current
      : (selectedPhotos[0]?.id ?? ""));
  }, [selectedPhotos]);

  const selectedAsset = selectedPhotos.find((asset) => asset.id === selectedAssetId) ?? null;
  const selectedAssetOnline = selectedAsset?.storage_status === "ONLINE" && Boolean(selectedAsset.content_url);

  useEffect(() => {
    setReplacementFile(null);
    setRestoreFile(null);
  }, [selectedAsset?.id]);

  useEffect(() => setMessage(""), [selectedGameId]);

  useEffect(() => {
    if (!seasonId || !selectedGameId) return;
    const params = new URLSearchParams({ page: "media", season_id: seasonId, game_id: selectedGameId });
    window.history.replaceState(null, "", `/?${params.toString()}`);
  }, [seasonId, selectedGameId]);

  const replace = async () => {
    if (!selectedAsset || !replacementFile) {
      setMessage("请先选择新的图片文件。");
      return;
    }
    if (!window.confirm(archived
      ? "确认在已归档赛季上传照片新版本？旧文件和操作者会保留在审计中，旧导出应视为过期。"
      : "确认替换当前照片？旧文件会保留审计记录。")) return;
    setBusy(true);
    setMessage("");
    try {
      const operation = `replace:${selectedAsset.id}:${selectedAsset.version}:${replacementFile.name}:${replacementFile.size}:${replacementFile.lastModified}`;
      const idempotencyKey = mediaOperationKeys.current.get(operation) ?? createIdempotencyKey();
      mediaOperationKeys.current.set(operation, idempotencyKey);
      await client.replaceAdminGameMedia(
        selectedAsset.id,
        selectedAsset.version,
        false,
        replacementFile,
        idempotencyKey,
      );
      mediaOperationKeys.current.delete(operation);
      setReplacementFile(null);
      setMessage("照片已重新上传，旧文件已保留审计记录。");
      await loadAssets();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "替换失败");
    } finally {
      setBusy(false);
    }
  };

  const uploadPhotos = async (
    kind: "GROUP_PHOTO" | "GAME_PHOTO",
    files: File[],
  ) => {
    if (!selectedGame || files.length === 0) return;
    if (archived && !window.confirm(
      `确认向已归档赛季添加 ${files.length} 张照片？新版本会保留操作者审计，旧导出应视为过期。`,
    )) return;
    setBusy(true);
    setMessage("");
    const remaining: File[] = [];
    const failures: string[] = [];
    let successCount = 0;
    let failedCount = 0;
    let unknownCount = 0;
    try {
      for (const file of files) {
        const operation = `upload:${selectedGame.game_id}:${kind}:${file.name}:${file.size}:${file.lastModified}`;
        const idempotencyKey = mediaOperationKeys.current.get(operation) ?? createIdempotencyKey();
        mediaOperationKeys.current.set(operation, idempotencyKey);
        try {
          await client.uploadAdminGameMedia(selectedGame.game_id, kind, false, file, idempotencyKey);
          mediaOperationKeys.current.delete(operation);
          successCount += 1;
        } catch (reason: unknown) {
          remaining.push(file);
          if (reason instanceof ApiError && reason.status >= 400 && reason.status < 500) {
            failedCount += 1;
            failures.push(`${file.name}：${reason.message}`);
          } else {
            unknownCount += 1;
            failures.push(`${file.name}：结果未确认`);
          }
        }
      }
      setRetryBatch(remaining.length ? { gameId: selectedGame.game_id, kind, files: remaining } : null);
      // A lost response can still have committed a photo. Always refresh the authoritative list.
      await loadAssets();
      setMessage(remaining.length
        ? `已上传 ${successCount} 张，失败 ${failedCount} 张，结果未确认 ${unknownCount} 张。${failures.join("；")}`
        : kind === "GROUP_PHOTO" ? "比赛合照已上传并公开。" : `已添加 ${files.length} 张其他照片。`);
    } finally {
      setBusy(false);
    }
  };

  const remove = async () => {
    if (!selectedAsset || !window.confirm(archived
      ? "确认在已归档赛季软删除这张照片？历史版本仍保留，旧导出应视为过期。"
      : "确认从在线资料中删除这张照片？操作会写入审计日志。")) return;
    setBusy(true);
    setMessage("");
    try {
      await client.deleteAdminGameMedia(selectedAsset.id, selectedAsset.version);
      setMessage("照片已从在线资料中删除。");
      await loadAssets();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "删除失败");
    } finally {
      setBusy(false);
    }
  };

  const restore = async () => {
    if (!selectedAsset || !restoreFile) return;
    if (!window.confirm("确认用所选文件恢复归档原图？系统会逐项核对原哈希、大小、格式和像素尺寸。")) return;
    setBusy(true);
    setMessage("");
    try {
      const operation = `restore:${selectedAsset.id}:${selectedAsset.version}:${restoreFile.name}:${restoreFile.size}:${restoreFile.lastModified}`;
      const idempotencyKey = mediaOperationKeys.current.get(operation) ?? createIdempotencyKey();
      mediaOperationKeys.current.set(operation, idempotencyKey);
      await client.restoreAdminGameMedia(
        selectedAsset.id,
        selectedAsset.version,
        restoreFile,
        idempotencyKey,
      );
      mediaOperationKeys.current.delete(operation);
      setRestoreFile(null);
      setMessage("归档原图已按原哈希恢复上线，旧归档元数据仍保留在审计中。");
      await loadAssets();
    } catch (reason: unknown) {
      setMessage(reason instanceof Error ? reason.message : "归档原图恢复失败");
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="competition-media-workbench">
      <header className="media-workbench-toolbar">
        <div className="media-filter-grid">
          <label>
            <span>赛季</span>
            <select value={seasonId} onChange={(event) => onSeasonChange(event.target.value)}>
              {seasons.map((item) => (
                <option value={item.id} key={item.id}>{formatAdminSeasonLabel(item)}</option>
              ))}
            </select>
          </label>
          <label className="media-search-field">
            <span>球队或比赛</span>
            <div><Search size={15} /><input value={query} onChange={(event) => { setGamesPage(1); setQuery(event.target.value); }} placeholder="搜索球队、日期或场地" /></div>
          </label>
          <label>
            <span>组别</span>
            <select value={division} onChange={(event) => { setGamesPage(1); setDivision(event.target.value); }}>
              <option value="">全部组别</option>
              {divisionNames.map((item) => <option value={item} key={item}>{item}</option>)}
            </select>
          </label>
          <label>
            <span>处理状态</span>
            <select value={processing} onChange={(event) => { setGamesPage(1); setProcessing(event.target.value as ProcessingFilter); }}>
              <option value="">全部状态</option>
              <option value="UPLOAD">待上传记录表</option>
              <option value="SCORESHEET_REVIEW">待核对记录表</option>
              <option value="COMPLETE">资料已完成</option>
            </select>
          </label>
        </div>
        <div className="media-summary-line" aria-label="比赛资料摘要">
          <span><strong>{gamesTotal}</strong> 场符合条件</span>
          <span>第 <strong>{gamesPage}</strong> / {Math.max(1, Math.ceil(gamesTotal / pageSize))} 页</span>
          <button type="button" onClick={() => { void loadGames(); void loadAssets(); }} disabled={gamesLoading || assetsLoading}>
            <RefreshCw size={14} className={gamesLoading || assetsLoading ? "spin" : undefined} />刷新
          </button>
        </div>
      </header>

      {archived && (
        <div className="media-archive-notice" role="status">
          <Archive size={17} />
          <span><strong>已归档赛季</strong>　普通管理员只读；超级管理员可版本化上传、替换或软删除普通照片，并按原哈希恢复离线文件。</span>
        </div>
      )}

      <div className="media-workbench-layout">
        <aside className="media-game-index" aria-label="比赛索引">
          <div className="media-index-heading">
            <div><h2>比赛索引</h2><p>按比赛集中处理记录表与照片</p></div>
            <strong>{visibleGames.length}</strong>
          </div>
          {gamesError ? (
            <div className="media-inline-error"><AlertTriangle size={17} /><span>{gamesError}</span></div>
          ) : (
            <div className="media-game-list">
              {visibleGames.map((game) => {
                const photos = (assetsByGame.get(game.game_id) ?? []).filter((asset) => asset.kind !== "SCORESHEET");
                return (
                  <button
                    type="button"
                    className={game.game_id === selectedGameId ? "media-game-row active" : "media-game-row"}
                    key={game.game_id}
                    onClick={() => setSelectedGameId(game.game_id)}
                  >
                    <span className="media-game-when"><strong>{formatDate(game.date)}</strong><small>{game.start_time} · {game.division_name}</small></span>
                    <span className="media-game-matchup"><strong>{game.home_name}</strong><em aria-hidden>—</em><strong>{game.away_name}</strong></span>
                    <span className="media-game-meta">
                      <span className={`scoresheet-state state-${game.status.toLocaleLowerCase()}`}>{scoresheetStatusLabels[game.status] ?? game.status}</span>
                      <span><ImageIcon size={13} />{photos.length}</span>
                    </span>
                  </button>
                );
              })}
              {!gamesLoading && !visibleGames.length && <div className="media-empty-state"><strong>没有匹配的比赛</strong><span>请调整搜索、组别或处理状态。</span></div>}
            </div>
          )}
          {gamesTotal > 0 && (
            <nav className="media-index-pagination" aria-label="比赛资料分页">
              <button type="button" disabled={gamesLoading || gamesPage <= 1} onClick={() => setGamesPage((value) => value - 1)}>上一页</button>
              <span>{gamesPage} / {Math.max(1, Math.ceil(gamesTotal / pageSize))}</span>
              <button type="button" disabled={gamesLoading || gamesPage * pageSize >= gamesTotal} onClick={() => setGamesPage((value) => value + 1)}>下一页</button>
            </nav>
          )}
        </aside>

        <main className="media-game-detail">
          {!selectedGame ? (
            <div className="media-empty-state detail"><FileText size={24} /><strong>{gamesLoading ? "正在读取比赛资料" : "请选择一场比赛"}</strong><span>记录表与比赛照片会在这里连续显示。</span></div>
          ) : (
            <>
              <header className="media-game-header">
                <div>
                  <p>{selectedGame.division_name}</p>
                  <h2><span>{selectedGame.home_name}</span><em aria-hidden>—</em><span>{selectedGame.away_name}</span></h2>
                </div>
                <dl>
                  <div><dt><CalendarDays size={14} />比赛时间</dt><dd>{selectedGame.date}　{selectedGame.start_time}</dd></div>
                  <div><dt><MapPin size={14} />比赛场地</dt><dd>{selectedGame.venue || "待定"}</dd></div>
                </dl>
              </header>

              <section className="media-detail-section scoresheet-section">
                <div className="media-section-heading">
                  <div><span>01</span><div><h3>记录表</h3><p>识别、人工核对、校验与发布在同一流程中完成。</p></div></div>
                  <strong>{scoresheetStatusLabels[selectedGame.status] ?? selectedGame.status}</strong>
                </div>
                <ol className="scoresheet-progress" aria-label="记录表处理进度">
                  {scoresheetProgress(selectedGame).map((step) => <li className={step.state} key={step.label}>{step.label}</li>)}
                </ol>
                {selectedGame.recognition_status && (
                  <p className="scoresheet-recognition-copy">
                    当前识别任务：{recognitionLabel(selectedGame.recognition_status)}
                    {selectedGame.recognition_attempt ? ` · 第 ${selectedGame.recognition_attempt}/${selectedGame.recognition_max_attempts} 次尝试` : ""}
                  </p>
                )}
                <div className="scoresheet-primary-row">
                  <div><strong>{scoresheetActionTitle(selectedGame)}</strong><span>{scoresheetActionDetail(selectedGame, archived)}</span></div>
                  {!archived && (
                    <button className="media-primary-action" type="button" onClick={() => window.location.assign(scoresheetHref(seasonId, selectedGame.game_id))}>
                      {scoresheetActionLabel(selectedGame)}<ExternalLink size={15} />
                    </button>
                  )}
                  {archived && selectedGame.scoresheet_id && (
                    <div className="media-archived-scoresheet-actions">
                      <button className="media-secondary-action" type="button" onClick={() => window.location.assign(scoresheetHref(seasonId, selectedGame.game_id, { archivedView: true }))}>
                        查看记录表<ExternalLink size={15} />
                      </button>
                      {isSuperadmin && selectedGame.status === "PUBLISHED" && (
                        <button
                          className="media-primary-action"
                          type="button"
                          onClick={() => {
                            if (window.confirm("确认纠正这张已归档记录表？修改必须重新校验并发布，新旧版本和操作者都会保留在审计中。")) {
                              window.location.assign(scoresheetHref(seasonId, selectedGame.game_id, {
                                archivedView: true,
                                correctionDocumentId: selectedGame.scoresheet_id ?? "",
                              }));
                            }
                          }}
                        >
                          受控纠错<ExternalLink size={15} />
                        </button>
                      )}
                    </div>
                  )}
                </div>
              </section>

              <section className="media-detail-section photos-section">
                <div className="media-section-heading">
                  <div><span>02</span><div><h3>比赛照片</h3><p>比赛合照上传后立即公开，其他照片仅供内部查阅。</p></div></div>
                </div>
                {assetsError && <div className="media-inline-error"><AlertTriangle size={17} /><span>{assetsError}。记录表状态仍可继续处理。</span></div>}
                {!assetsError && (
                  <>
                    <PhotoCategory
                      title="比赛合照"
                      emptyCopy="尚未上传比赛合照"
                      assets={selectedPhotos.filter((asset) => asset.kind === "GROUP_PHOTO")}
                      selectedId={selectedAssetId}
                      onSelect={setSelectedAssetId}
                      action={(!archived || isSuperadmin) && !selectedPhotos.some((asset) => asset.kind === "GROUP_PHOTO") ? (
                        <PhotoUploadButton disabled={busy} label="上传比赛合照" onFiles={(files) => void uploadPhotos("GROUP_PHOTO", files.slice(0, 1))} />
                      ) : null}
                    />
                    <PhotoCategory
                      title="其他照片"
                      emptyCopy="尚未添加其他照片"
                      assets={selectedPhotos.filter((asset) => asset.kind === "GAME_PHOTO")}
                      selectedId={selectedAssetId}
                      onSelect={setSelectedAssetId}
                      action={!archived || isSuperadmin ? (
                        <PhotoUploadButton multiple disabled={busy} label="添加其他照片" onFiles={(files) => void uploadPhotos("GAME_PHOTO", files)} />
                      ) : null}
                    />
                    {!selectedPhotos.length && !assetsLoading && <div className="media-empty-state compact"><ImageIcon size={21} /><strong>尚无比赛照片</strong><span>领队或管理员上传后会显示在这里。</span></div>}
                  </>
                )}

                {selectedAsset && (
                  <div className="photo-inspector">
                    <div className="photo-preview-panel">
                      {selectedAssetOnline ? (
                        <a href={selectedAsset.content_url} target="_blank" rel="noreferrer"><img src={selectedAsset.content_url} alt={`${selectedGame.game_label} ${mediaKindLabel(selectedAsset.kind)}`} /></a>
                      ) : (
                        <div className="photo-offline-state"><Archive size={24} /><strong>原图已离线归档</strong><span>元数据和校验信息仍保留。</span></div>
                      )}
                    </div>
                    <div className="photo-inspector-panel">
                      <div className="photo-status-line"><strong>{mediaKindLabel(selectedAsset.kind)}</strong></div>
                      <dl className="photo-metadata">
                        <div><dt>上传者</dt><dd>{selectedAsset.uploaded_by}</dd></div>
                        <div><dt>存储</dt><dd>{selectedAssetOnline ? "在线原图" : "离线归档"}</dd></div>
                      </dl>
                      {selectedAssetOnline && selectedAsset.can_replace && (
                        <div className="photo-replacement-control">
                          <label>替换照片<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={(event) => setReplacementFile(event.target.files?.[0] ?? null)} /></label>
                          <button type="button" disabled={busy || !replacementFile} onClick={() => void replace()}>上传替换</button>
                        </div>
                      )}
                      {selectedAsset.can_restore && (
                        <div className="photo-replacement-control archived-restore">
                          <label>选择归档原文件<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy} onChange={(event) => setRestoreFile(event.target.files?.[0] ?? null)} /></label>
                          <small>仅哈希、大小、格式和像素尺寸全部匹配时恢复。</small>
                          <button type="button" disabled={busy || !restoreFile} onClick={() => void restore()}>核对并恢复</button>
                        </div>
                      )}
                      {selectedAsset.can_delete && <div className="photo-delete-actions">
                        <button type="button" className="delete" disabled={busy} onClick={() => void remove()}>删除照片</button>
                      </div>}
                    </div>
                  </div>
                )}
      {message && <p className="media-operation-message" role="status">{message}</p>}
      {!archived && retryBatch?.gameId === selectedGameId && (
        <button type="button" className="media-secondary-action" disabled={busy}
          onClick={() => void uploadPhotos(retryBatch.kind, retryBatch.files)}>
          仅重试未完成的照片
        </button>
      )}
              </section>
            </>
          )}
        </main>
      </div>
    </section>
  );
}

function PhotoCategory({ title, emptyCopy, assets, selectedId, onSelect, action }: {
  title: string;
  emptyCopy: string;
  assets: GameMediaAsset[];
  selectedId: string;
  onSelect: (assetId: string) => void;
  action: ReactNode;
}) {
  return (
    <div className="photo-strip-group">
      <div className="photo-category-heading">
        <h4>{title}</h4>
        {action}
      </div>
      {assets.length ? (
        <div className="photo-thumbnail-strip">
          {assets.map((asset) => (
            <button type="button" className={asset.id === selectedId ? "active" : ""} key={asset.id} onClick={() => onSelect(asset.id)}>
              {asset.storage_status === "ONLINE" && asset.content_url ? <img src={asset.content_url} alt="" /> : <span><Archive size={18} />已归档</span>}
            </button>
          ))}
        </div>
      ) : <p className="photo-category-empty">{emptyCopy}</p>}
    </div>
  );
}

function PhotoUploadButton({ label, disabled, multiple = false, onFiles }: {
  label: string;
  disabled: boolean;
  multiple?: boolean;
  onFiles: (files: File[]) => void;
}) {
  return (
    <label className={disabled ? "photo-add-button disabled" : "photo-add-button"}>
      {label}
      <input
        type="file"
        accept="image/jpeg,image/png,image/webp"
        multiple={multiple}
        disabled={disabled}
        onChange={(event) => {
          const files = Array.from(event.target.files ?? []);
          event.target.value = "";
          onFiles(files);
        }}
      />
    </label>
  );
}

function scoresheetProgress(game: ScoresheetQueueItem) {
  const current = game.status === "PUBLISHED"
    ? 4
    : game.status === "READY"
      ? 3
      : game.source_asset_id && !recognitionStatuses.has(game.status)
        ? 2
        : game.source_asset_id
          ? 1
          : 0;
  return ["上传", "识别", "人工核对", "校验", "发布"].map((label, index) => ({
    label,
    state: index < current ? "done" : index === current ? "current" : "pending",
  }));
}

function scoresheetActionLabel(game: ScoresheetQueueItem) {
  if (!game.source_asset_id) return "上传并识别";
  if (game.status === "PUBLISHED") return "查看已发布记录表";
  return "继续核对";
}

function scoresheetActionTitle(game: ScoresheetQueueItem) {
  if (!game.source_asset_id) return "尚未上传记录表原图";
  if (game.status === "PUBLISHED") return `第 ${game.publication_number ?? 1} 版记录表已发布`;
  if (recognitionStatuses.has(game.status)) return "识别任务正在处理原图";
  if (game.status === "RECOGNITION_FAILED") return "自动识别未完成，可进入编辑器人工处理";
  return "记录表需要人工核对或发布";
}

function scoresheetActionDetail(game: ScoresheetQueueItem, archived: boolean) {
  if (archived) return "归档资料默认只读；超级管理员明确确认后可纠错并发布新版本。";
  if (!game.source_asset_id) return "进入全屏编辑器后选择照片，上传完成会自动开始识别。";
  if (game.status === "PUBLISHED") return "可查看已发布内容和导出文件；后续修改仍受编辑租约保护。";
  return "进入全屏编辑器继续校对语义字段、校验并发布。";
}

export function scoresheetHref(
  seasonId: string,
  gameId: string,
  options: { archivedView?: boolean; correctionDocumentId?: string } = {},
) {
  const params = new URLSearchParams({ season_id: seasonId, game_id: gameId });
  if (options.archivedView) params.set("archived_view", "1");
  if (options.correctionDocumentId) {
    params.set("archived_correction", options.correctionDocumentId);
  }
  return `/scoresheet.html?${params.toString()}`;
}

function recognitionLabel(status: string) {
  return ({
    pending: "排队中", connecting: "连接识别服务", thinking: "识别中", structuring: "整理字段",
    validating: "检查结果", succeeded: "识别完成", failed: "识别失败", interrupted: "识别中断",
  } as Record<string, string>)[status.toLocaleLowerCase()] ?? status;
}

function mediaKindLabel(kind: string) {
  return ({ GROUP_PHOTO: "比赛合照", GAME_PHOTO: "其他照片" } as Record<string, string>)[kind] ?? kind;
}

function formatDate(value: string) {
  const [, month, day] = value.split("-");
  return month && day ? `${Number(month)}月${Number(day)}日` : value;
}
