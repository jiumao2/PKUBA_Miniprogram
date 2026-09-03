import { AlertTriangle, LoaderCircle, X } from 'lucide-react';
import { useEffect, useMemo, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import { api } from './api';
import { DocumentCanvas } from './components/DocumentCanvas';
import { GameBrowser } from './components/GameBrowser';
import { Inspector } from './components/Inspector';
import { PaneResizer } from './components/PaneResizer';
import { SourcePane } from './components/SourcePane';
import { ScoresheetLogo } from './components/ScoresheetLogo';
import { TopBar } from './components/TopBar';
import { pathToField } from './lib/fieldPaths';
import { useEditorStore } from './store';

type PaneKind = 'source' | 'inspector';

interface PaneLayout {
  source: number;
  inspector: number;
}

const DEFAULT_PANE_LAYOUT: PaneLayout = { source: 0.23, inspector: 0.27 };
const PANE_LAYOUT_KEY = 'scoresheet-reader:pane-layout';
const SOURCE_MIN = 260;
const INSPECTOR_MIN = 320;
const DOCUMENT_MIN = 520;
const RESIZER_SIZE = 10;

function readPaneLayout(): PaneLayout {
  try {
    const stored = JSON.parse(localStorage.getItem(PANE_LAYOUT_KEY) ?? '{}') as Partial<PaneLayout>;
    if (
      typeof stored.source === 'number' &&
      typeof stored.inspector === 'number' &&
      stored.source > 0.1 && stored.source < 0.6 &&
      stored.inspector > 0.1 && stored.inspector < 0.6
    ) {
      return { source: stored.source, inspector: stored.inspector };
    }
  } catch {
    // A malformed local preference should never prevent the editor from opening.
  }
  return DEFAULT_PANE_LAYOUT;
}

function sameLayout(left: PaneLayout, right: PaneLayout) {
  return Math.abs(left.source - right.source) < 0.0005 && Math.abs(left.inspector - right.inspector) < 0.0005;
}

function normalizePaneLayout(
  layout: PaneLayout,
  width: number,
  sourceOpen: boolean,
  inspectorOpen: boolean,
): PaneLayout {
  if (width <= 0) return layout;
  const dividerCount = Number(sourceOpen) + Number(inspectorOpen);
  const available = Math.max(0, width - DOCUMENT_MIN - dividerCount * RESIZER_SIZE);
  let sourcePixels = sourceOpen ? Math.max(SOURCE_MIN, layout.source * width) : 0;
  let inspectorPixels = inspectorOpen ? Math.max(INSPECTOR_MIN, layout.inspector * width) : 0;

  if (sourceOpen && inspectorOpen && sourcePixels + inspectorPixels > available) {
    const flexibleSource = Math.max(0, sourcePixels - SOURCE_MIN);
    const flexibleInspector = Math.max(0, inspectorPixels - INSPECTOR_MIN);
    const flexibleTotal = flexibleSource + flexibleInspector;
    const allowedFlexible = Math.max(0, available - SOURCE_MIN - INSPECTOR_MIN);
    if (flexibleTotal > 0) {
      sourcePixels = SOURCE_MIN + allowedFlexible * (flexibleSource / flexibleTotal);
      inspectorPixels = INSPECTOR_MIN + allowedFlexible * (flexibleInspector / flexibleTotal);
    } else {
      sourcePixels = SOURCE_MIN;
      inspectorPixels = INSPECTOR_MIN;
    }
  } else if (sourceOpen) {
    sourcePixels = Math.min(sourcePixels, available - inspectorPixels);
  } else if (inspectorOpen) {
    inspectorPixels = Math.min(inspectorPixels, available);
  }

  return {
    source: sourceOpen ? Math.max(SOURCE_MIN, sourcePixels) / width : layout.source,
    inspector: inspectorOpen ? Math.max(INSPECTOR_MIN, inspectorPixels) / width : layout.inspector,
  };
}

export default function App() {
  const state = useEditorStore();
  const editorRoute = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return {
      seasonId: params.get('season_id') ?? '',
      gameId: params.get('game_id') ?? '',
    };
  }, []);
  const [sourceOpen, setSourceOpen] = useState(true);
  const [inspectorOpen, setInspectorOpen] = useState(true);
  const [paneLayout, setPaneLayout] = useState<PaneLayout>(readPaneLayout);
  const [activeResizer, setActiveResizer] = useState<PaneKind | null>(null);
  const [gameBrowserOpen, setGameBrowserOpen] = useState(false);
  const workspaceRef = useRef<HTMLElement>(null);
  const confirmedDepartureRef = useRef(false);
  const initialGameAppliedRef = useRef(false);
  const resizeSession = useRef<{
    kind: PaneKind;
    startX: number;
    width: number;
    layout: PaneLayout;
  } | null>(null);

  useEffect(() => {
    void state.initialize();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const sync = () => void state.syncNow();
    const onOffline = () => state.setOnline(false);
    const onOnline = () => state.setOnline(true);
    const syncTimer = window.setInterval(sync, 2_000);
    const heartbeatTimer = window.setInterval(() => void state.heartbeatLease(), 15_000);
    const onVisibility = () => {
      if (document.visibilityState === 'visible') sync();
    };
    window.addEventListener('focus', sync);
    window.addEventListener('online', onOnline);
    window.addEventListener('offline', onOffline);
    document.addEventListener('visibilitychange', onVisibility);
    return () => {
      window.clearInterval(syncTimer);
      window.clearInterval(heartbeatTimer);
      window.removeEventListener('focus', sync);
      window.removeEventListener('online', onOnline);
      window.removeEventListener('offline', onOffline);
      document.removeEventListener('visibilitychange', onVisibility);
      void state.releaseLease();
    };
  }, [state.syncNow, state.heartbeatLease, state.releaseLease, state.setOnline]);

  useEffect(() => {
    if (!state.dirty) return;
    const timeout = window.setTimeout(() => void state.save(), 750);
    return () => window.clearTimeout(timeout);
  }, [state.dirty, state.document, state.save]);

  useEffect(() => {
    if (!state.dirty) return;
    const warnBeforeUnload = (event: BeforeUnloadEvent) => {
      if (confirmedDepartureRef.current) return;
      event.preventDefault();
      event.returnValue = '';
    };
    window.addEventListener('beforeunload', warnBeforeUnload);
    return () => window.removeEventListener('beforeunload', warnBeforeUnload);
  }, [state.dirty]);

  useEffect(() => {
    if (state.loading || !editorRoute.gameId || initialGameAppliedRef.current) return;
    initialGameAppliedRef.current = true;
    const requestedGame = state.games.find((game) => game.id === editorRoute.gameId);
    if (!requestedGame?.document_id) setGameBrowserOpen(true);
  }, [editorRoute.gameId, state.games, state.loading]);

  useEffect(() => {
    if (!state.document) return;
    void state.refreshChanges();
  }, [state.document?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      const modifier = event.ctrlKey || event.metaKey;
      if (!modifier) return;
      if (event.key.toLowerCase() === 'z') {
        event.preventDefault();
        if (event.shiftKey) state.redo();
        else state.undo();
      }
      if (event.key.toLowerCase() === 's') {
        event.preventDefault();
        void state.save();
      }
    };
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [state.undo, state.redo, state.save]);

  useEffect(() => {
    try {
      localStorage.setItem(PANE_LAYOUT_KEY, JSON.stringify(paneLayout));
    } catch {
      // The layout remains usable when storage is unavailable.
    }
  }, [paneLayout]);

  useEffect(() => {
    const workspace = workspaceRef.current;
    if (!workspace || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => {
      setPaneLayout((current) => {
        const normalized = normalizePaneLayout(current, entry.contentRect.width, sourceOpen, inspectorOpen);
        return sameLayout(current, normalized) ? current : normalized;
      });
    });
    observer.observe(workspace);
    return () => observer.disconnect();
  }, [sourceOpen, inspectorOpen]);

  useEffect(() => {
    document.body.classList.toggle('is-resizing-panes', activeResizer !== null);
    return () => document.body.classList.remove('is-resizing-panes');
  }, [activeResizer]);

  const beginResize = (kind: PaneKind, startX: number) => {
    const width = workspaceRef.current?.getBoundingClientRect().width ?? 0;
    if (!width) return;
    resizeSession.current = { kind, startX, width, layout: paneLayout };
    setActiveResizer(kind);
  };

  const moveResize = (kind: PaneKind, clientX: number) => {
    const session = resizeSession.current;
    if (!session || session.kind !== kind) return;
    const delta = clientX - session.startX;
    const dividerCount = Number(sourceOpen) + Number(inspectorOpen);
    const fixedOther = kind === 'source'
      ? (inspectorOpen ? session.layout.inspector * session.width : 0)
      : (sourceOpen ? session.layout.source * session.width : 0);
    const minimum = kind === 'source' ? SOURCE_MIN : INSPECTOR_MIN;
    const maximum = Math.max(
      minimum,
      session.width - fixedOther - DOCUMENT_MIN - dividerCount * RESIZER_SIZE,
    );
    const startPixels = session.layout[kind] * session.width;
    const desired = kind === 'source' ? startPixels + delta : startPixels - delta;
    const nextPixels = Math.min(maximum, Math.max(minimum, desired));
    setPaneLayout({ ...session.layout, [kind]: nextPixels / session.width });
  };

  const finishResize = () => {
    resizeSession.current = null;
    setActiveResizer(null);
  };

  const keyboardResize = (kind: PaneKind, separatorDirection: -1 | 1) => {
    const width = workspaceRef.current?.getBoundingClientRect().width ?? 0;
    if (!width) return;
    const paneDirection = kind === 'source' ? separatorDirection : -separatorDirection;
    const dividerCount = Number(sourceOpen) + Number(inspectorOpen);
    setPaneLayout((current) => {
      const fixedOther = kind === 'source'
        ? (inspectorOpen ? current.inspector * width : 0)
        : (sourceOpen ? current.source * width : 0);
      const minimum = kind === 'source' ? SOURCE_MIN : INSPECTOR_MIN;
      const maximum = Math.max(minimum, width - fixedOther - DOCUMENT_MIN - dividerCount * RESIZER_SIZE);
      const nextPixels = Math.min(maximum, Math.max(minimum, current[kind] * width + paneDirection * 24));
      return { ...current, [kind]: nextPixels / width };
    });
  };

  const workspaceStyle = {
    '--source-pane-size': `${paneLayout.source * 100}%`,
    '--inspector-pane-size': `${paneLayout.inspector * 100}%`,
  } as CSSProperties;
  const effectiveReadOnly = state.readOnly || !state.online || state.contextReviewing;
  const archivedRoute = useMemo(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('archived_view') === '1' || Boolean(params.get('archived_correction'));
  }, []);
  const returnHref = useMemo(() => {
    const gameId = state.document?.game_prior?.game_id || editorRoute.gameId;
    const params = new URLSearchParams({ page: 'media' });
    if (state.seasonId || editorRoute.seasonId) params.set('season_id', state.seasonId || editorRoute.seasonId);
    if (gameId) params.set('game_id', gameId);
    return `/?${params.toString()}`;
  }, [editorRoute.gameId, editorRoute.seasonId, state.document?.game_prior?.game_id, state.seasonId]);

  const returnToMedia = async () => {
    if (state.dirty && !window.confirm('当前记录表还有未保存修改，确认离开并返回比赛资料吗？')) return;
    confirmedDepartureRef.current = true;
    await state.releaseLease();
    window.location.assign(returnHref);
  };

  const anomalyFields = useMemo(() => {
    const fields = new Set<string>();
    if (!state.document) return fields;
    state.document.recognition?.problem_paths.forEach((path) => {
      fields.add(pathToField(path, state.document!));
    });
    state.validation?.issues.forEach((issue) => issue.paths.forEach((path) => {
      fields.add(pathToField(path, state.document!));
    }));
    return fields;
  }, [state.document, state.validation]);

  if (state.loading && !state.template) {
    return (
      <main className="loading-screen">
        <ScoresheetLogo className="scoresheet-logo loading-logo" title="ScoresheetReader 记录表" />
        <LoaderCircle className="spin" size={22} />
        <p>正在打开记录表工作台…</p>
      </main>
    );
  }

  if (!state.template) {
    return (
      <main className="loading-screen error-screen">
        <AlertTriangle size={28} />
        <h1>无法启动编辑器</h1>
        <p>{state.error || '模板定义未加载，请刷新页面重试。'}</p>
      </main>
    );
  }

  const pendingCorrection = state.document
    ? api.pendingCorrection(state.document.id)
    : null;

  return (
    <div className="app-shell">
      <TopBar
        document={state.document}
        validation={state.validation}
        saveState={state.saveState}
        canUndo={state.past.length > 0}
        canRedo={state.future.length > 0}
        recognitionMode={state.recognitionMode}
        recognitionState={state.recognitionState}
        recognitionRetryAllowed={state.recognitionRun?.can_retry === true}
        onChooseGame={() => setGameBrowserOpen(true)}
        onRecognize={state.recognize}
        onUndo={state.undo}
        onRedo={state.redo}
        onSave={state.save}
        onValidate={state.validate}
        onConfirm={state.confirm}
        publicationRecoveryAllowed={Boolean(state.document && !state.dirty && state.online && !state.contextReviewing
          && api.hasPendingPublication(state.document.id, state.serverRevision))}
        sourceOpen={sourceOpen}
        inspectorOpen={inspectorOpen}
        onToggleSource={() => setSourceOpen((value) => !value)}
        onToggleInspector={() => setInspectorOpen((value) => !value)}
        readOnly={effectiveReadOnly}
        readOnlyReason={state.online ? state.readOnlyReason : '网络已断开；恢复后会先同步服务器版本。'}
        online={state.online}
        leaseHolder={state.leaseHolder}
        onReturn={() => void returnToMedia()}
      />
      {pendingCorrection && (
        <div className="pending-correction-banner" role="status">
          <AlertTriangle size={17} />
          <div>
            <strong>正在复核一笔比赛纠错</strong>
            <span>本次发布会在同一事务中切换参赛方、比分、赛果版本和当前公开记录表；旧发布与旧赛果版本不会删除。</span>
            {pendingCorrection.reason && <small>理由：{pendingCorrection.reason}</small>}
          </div>
        </div>
      )}
      <main
        ref={workspaceRef}
        className={`workspace${sourceOpen ? '' : ' source-collapsed'}${inspectorOpen ? '' : ' inspector-collapsed'}`}
        style={workspaceStyle}
      >
        {sourceOpen ? (
          <>
            <SourcePane
              document={state.document}
              onRequestUpload={() => setGameBrowserOpen(true)}
              onReloadSource={state.reloadSource}
              allowUpload={!archivedRoute && (!state.document || state.canUploadSource)}
            />
            <PaneResizer
              label="调整原图与标准记录表宽度"
              value={paneLayout.source}
              active={activeResizer === 'source'}
              onDragStart={(clientX) => beginResize('source', clientX)}
              onDrag={(clientX) => moveResize('source', clientX)}
              onDragEnd={finishResize}
              onKeyboardMove={(direction) => keyboardResize('source', direction)}
              onReset={() => setPaneLayout((current) => ({ ...current, source: DEFAULT_PANE_LAYOUT.source }))}
            />
          </>
        ) : null}
        <DocumentCanvas
          document={state.document}
          definition={state.template}
          selectedField={state.selectedField}
          onSelect={state.selectField}
          anomalyFields={anomalyFields}
        />
        {inspectorOpen ? (
          <>
            <PaneResizer
              label="调整标准记录表与编辑面板宽度"
              value={paneLayout.inspector}
              active={activeResizer === 'inspector'}
              onDragStart={(clientX) => beginResize('inspector', clientX)}
              onDrag={(clientX) => moveResize('inspector', clientX)}
              onDragEnd={finishResize}
              onKeyboardMove={(direction) => keyboardResize('inspector', direction)}
              onReset={() => setPaneLayout((current) => ({ ...current, inspector: DEFAULT_PANE_LAYOUT.inspector }))}
            />
            {state.document ? (
              <Inspector
                document={state.document}
                selectedField={state.selectedField}
                validation={state.validation}
                changes={state.changes}
                recognitionRun={state.recognitionRun}
                recognitionDiff={state.recognitionDiff}
                recognitionState={state.recognitionState}
                onMutate={state.mutate}
                onSelect={state.selectField}
                onApplyRecognition={state.applyRecognition}
                onDismissRecognitionDiff={state.clearRecognitionDiff}
                onReviewGameContext={state.reviewGameContext}
                contextReviewing={state.contextReviewing}
                readOnly={effectiveReadOnly}
              />
            ) : (
              <aside className="inspector empty-inspector" aria-label="语义检查器">
                <header className="inspector-context">
                  <div>
                    <span className="pane-kicker">当前状态</span>
                    <strong>尚未选择比赛</strong>
                  </div>
                </header>
                <div className="empty-inspector-content">
                  <ScoresheetLogo className="scoresheet-logo empty-inspector-logo" />
                  <strong>从一场真实比赛开始</strong>
                  <p>选择比赛并上传记录表照片后，识别结果与语义编辑控件会显示在这里。</p>
                  <button className="primary-action" onClick={() => setGameBrowserOpen(true)}>选择比赛</button>
                </div>
              </aside>
            )}
          </>
        ) : null}
      </main>
      {gameBrowserOpen ? (
        <GameBrowser
          games={state.games.map(game => game.document_id === state.document?.id
            ? { ...game, can_upload_source: state.canUploadSource }
            : game)}
          total={state.gamesTotal}
          page={state.gamesPage}
          pageSize={state.gamesPageSize}
          scope={state.gamesScope}
          query={state.gamesQuery}
          loading={state.gamesLoading}
          onClose={() => setGameBrowserOpen(false)}
          onLoad={state.loadGames}
          onOpen={state.openDocument}
          onUpload={state.uploadForGame}
          onReupload={state.reupload}
          initialGameId={editorRoute.gameId}
          allowUploads={!archivedRoute}
        />
      ) : null}
      {state.error ? (
        <div className="error-toast" role="alert">
          <AlertTriangle size={17} />
          <span>{state.error}</span>
          {state.saveState === 'conflict' ? (
            <span className="conflict-actions">
              <button type="button" onClick={() => void state.reloadAfterConflict()}>载入服务器版本</button>
              <button type="button" onClick={() => void state.overwriteAfterConflict()}>保留本地并重试</button>
            </span>
          ) : null}
          <button className="toast-close" aria-label="关闭提示" onClick={() => useEditorStore.setState({ error: '' })}><X size={15} /></button>
        </div>
      ) : null}
    </div>
  );
}
