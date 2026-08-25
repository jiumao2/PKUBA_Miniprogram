import { CalendarDays, MapPin, RefreshCw, Search, Upload, X } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import type { GameQueueQuery, GameQueueScope, GameSummary } from '../types';

interface GameBrowserProps {
  games: GameSummary[];
  total: number;
  page: number;
  pageSize: number;
  scope: GameQueueScope;
  query: string;
  loading: boolean;
  onClose: () => void;
  onLoad: (query: GameQueueQuery) => Promise<void>;
  onOpen: (documentId: string) => Promise<void>;
  onUpload: (gameId: string, file: File) => Promise<void>;
  onReupload: (documentId: string, file: File) => Promise<void>;
  initialGameId?: string;
}

const stateLabels: Record<GameSummary['scoresheet_state'], string> = {
  not_uploaded: '待上传',
  recognizing: '识别中',
  recognized: '已识别',
  recognition_failed: '识别失败',
  confirmed: '已提交',
};

export function GameBrowser({
  games,
  total,
  page,
  pageSize,
  scope,
  query,
  loading,
  onClose,
  onLoad,
  onOpen,
  onUpload,
  onReupload,
  initialGameId = '',
}: GameBrowserProps) {
  const [queryDraft, setQueryDraft] = useState(query);
  const [selectedGame, setSelectedGame] = useState<GameSummary | null>(null);
  const [uploading, setUploading] = useState(false);
  const [openingGameId, setOpeningGameId] = useState<string | null>(null);
  const [reuploadDocumentId, setReuploadDocumentId] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const visibleGames = games;

  useEffect(() => setQueryDraft(query), [query]);

  useEffect(() => {
    if (queryDraft === query) return undefined;
    const timer = window.setTimeout(() => {
      void onLoad({ query: queryDraft, scope, page: 1, pageSize });
    }, queryDraft ? 250 : 0);
    return () => window.clearTimeout(timer);
  }, [onLoad, pageSize, query, queryDraft, scope]);

  useEffect(() => {
    if (!initialGameId) return;
    setSelectedGame((current) => current && games.some((game) => game.id === current.id)
      ? current
      : (games.find((game) => game.id === initialGameId) ?? games[0] ?? null));
  }, [games, initialGameId]);

  useEffect(() => {
    if (!games.some((game) => game.scoresheet_state === 'recognizing')) return undefined;
    const timer = window.setInterval(
      () => void onLoad({ query, scope, page, pageSize }),
      1500,
    );
    return () => window.clearInterval(timer);
  }, [games, onLoad, page, pageSize, query, scope]);

  return (
    <div className="game-browser-backdrop" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <section className="game-browser" role="dialog" aria-modal="true" aria-labelledby="game-browser-title">
        <header>
          <div>
            <span className="pane-kicker">赛程先验</span>
            <h2 id="game-browser-title">选择比赛</h2>
            <p>上传照片后自动开始识别；识别任务会在切换比赛后继续运行。</p>
          </div>
          <button className="icon-button" aria-label="关闭比赛列表" onClick={onClose}><X size={18} /></button>
        </header>
        <div className="game-browser-tools">
          <label className="game-search">
            <Search size={15} />
            <input value={queryDraft} onChange={(event) => setQueryDraft(event.target.value)} placeholder="搜索球队、组别、日期或地点" autoFocus />
          </label>
          <select
            aria-label="记录表状态"
            value={scope}
            onChange={(event) => void onLoad({
              query: queryDraft,
              scope: event.target.value as GameQueueScope,
              page: 1,
              pageSize,
            })}
          >
            <option value="ACTION_REQUIRED">待处理</option>
            <option value="IN_PROGRESS">识别中</option>
            <option value="PUBLISHED">已发布</option>
            <option value="ALL">全部</option>
          </select>
          <button className="secondary-action" onClick={() => void onLoad({ query, scope, page, pageSize })} disabled={loading}>
            <RefreshCw size={14} className={loading ? 'spin' : undefined} /> 刷新
          </button>
          <span>{visibleGames.length} / {total} 场</span>
        </div>
        <div className="game-list" aria-label="可选比赛">
          {visibleGames.map((game) => (
            <div className="game-row-shell" key={game.id}>
              <button
              className={`game-row${selectedGame?.id === game.id ? ' is-selected' : ''}${openingGameId === game.id ? ' is-opening' : ''}`}
              disabled={(!game.ready && !game.document_id) || uploading || openingGameId !== null}
              title={game.document_id ? '打开这场比赛的记录表' : (!game.ready ? game.unavailable_reason : '选择后上传记录表照片')}
              onClick={async () => {
                if (!game.document_id) {
                  setSelectedGame(game);
                  return;
                }
                setOpeningGameId(game.id);
                try {
                  await onOpen(game.document_id);
                  onClose();
                } catch {
                  // The editor store exposes the failure through its existing error toast.
                } finally {
                  setOpeningGameId(null);
                }
              }}
            >
              <span className="game-date"><CalendarDays size={14} />{game.date}<small>{game.scheduled_time}</small></span>
              <span className="game-matchup"><strong>{game.team_a_name}</strong><em aria-hidden>—</em><strong>{game.team_b_name}</strong><small>{game.division}</small></span>
              <span className="game-venue"><MapPin size={13} />{game.venue || '地点待定'}</span>
              <span className={`game-ready state-${game.document_id ? game.scoresheet_state : (game.ready ? 'not_uploaded' : 'disabled')}`}>
                {openingGameId === game.id ? '打开中' : (game.document_id ? stateLabels[game.scoresheet_state] : (game.ready ? '待上传' : '球队待定'))}
              </span>
              </button>
              {game.document_id && game.ready ? (
                <button
                  type="button"
                  className="game-reupload-button"
                  disabled={uploading || openingGameId !== null}
                  onClick={() => {
                    if (!window.confirm('重新上传会清空当前记录表内容和识别结果，并立即重新识别。确定继续吗？')) return;
                    setReuploadDocumentId(game.document_id);
                    fileInput.current?.click();
                  }}
                >
                  重新上传
                </button>
              ) : null}
            </div>
          ))}
          {!loading && visibleGames.length === 0 ? <div className="game-list-empty">没有匹配的比赛</div> : null}
        </div>
        {total > 0 ? (
          <nav className="game-browser-pagination" aria-label="记录表比赛分页">
            <button
              type="button"
              disabled={loading || page <= 1}
              onClick={() => void onLoad({ query, scope, page: page - 1, pageSize })}
            >
              上一页
            </button>
            <span>{page} / {Math.max(1, Math.ceil(total / pageSize))}</span>
            <button
              type="button"
              disabled={loading || page * pageSize >= total}
              onClick={() => void onLoad({ query, scope, page: page + 1, pageSize })}
            >
              下一页
            </button>
          </nav>
        ) : null}
        <footer>
          <div>
            {selectedGame ? <><strong>{selectedGame.team_a_name} — {selectedGame.team_b_name}</strong><span>名单不含球衣号码，号码仍由图片读取</span></> : <span>选择未上传比赛以导入照片；已有结果可直接点击打开</span>}
          </div>
          <input
            ref={fileInput}
            className="visually-hidden"
            type="file"
            accept="image/jpeg,image/png,image/webp"
            onChange={async (event) => {
              const file = event.target.files?.[0];
              if (!file || (!selectedGame && !reuploadDocumentId)) return;
              setUploading(true);
              try {
                if (reuploadDocumentId) {
                  await onReupload(reuploadDocumentId, file);
                } else if (selectedGame) {
                  await onUpload(selectedGame.id, file);
                }
                onClose();
              } catch {
                // The editor store exposes save or upload failures through its error toast.
              } finally {
                setUploading(false);
                setReuploadDocumentId(null);
                event.target.value = '';
              }
            }}
          />
          <button className="confirm-button" disabled={!selectedGame || uploading} onClick={() => fileInput.current?.click()}>
            <Upload size={15} /> {uploading ? '正在上传并排队…' : '上传并识别'}
          </button>
        </footer>
      </section>
    </div>
  );
}
