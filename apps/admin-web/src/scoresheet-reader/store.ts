import { create } from 'zustand';
import { hasRecognitionResult, type ScoresheetContextPlayerMapping } from '@pkuba/scoresheet-domain';
import { api } from './api';
import type {
  DocumentChangeLogEntry,
  GameQueueQuery,
  GameQueueScope,
  GameSummary,
  RecognitionDiff,
  RecognitionRun,
  ScoresheetDocument,
  TemplateDefinition,
  ValidationReport,
} from './types';
import { deepCloneDocument } from './types';
import { deriveScoreEvents } from './lib/score';

const LAST_DOCUMENT_KEY = 'scoresheet-reader:last-document-id';
const RECOGNITION_POLL_INTERVAL_MS = 500;
const RECOGNITION_POLL_LIMIT = 360;
let activeSave: Promise<void> | null = null;
let recognitionWatchGeneration = 0;
let gameQueueGeneration = 0;
// Returning to the same ID is a new editing session, even at the same revision.
let documentSessionGeneration = 0;
let contextReviewSequence = 0;

const wait = (milliseconds: number) =>
  new Promise<void>((resolve) => window.setTimeout(resolve, milliseconds));

async function loadRecognitionRun(
  document: ScoresheetDocument,
): Promise<RecognitionRun | null> {
  try {
    return await api.latestRecognition(document.id);
  } catch {
    return null;
  }
}

function isRestorableDocument(document: ScoresheetDocument): boolean {
  return Boolean(
    document.game_prior
    && document.source.original_url
    && document.id !== 'synthetic-preview',
  );
}

const activeRecognitionStatuses = new Set<RecognitionRun['status']>([
  'pending', 'connecting', 'thinking', 'structuring', 'validating',
]);

function recognitionStateFor(
  document: ScoresheetDocument,
  run: RecognitionRun | null,
): EditorState['recognitionState'] {
  if (run && activeRecognitionStatuses.has(run.status)) return 'running';
  if (run?.status === 'failed' || run?.status === 'interrupted') return 'failed';
  if (run?.status === 'succeeded' && !run.auto_applied) return 'diff';
  if (hasRecognitionResult(document)) return 'applied';
  return 'idle';
}

function rebaseSnapshot(document: ScoresheetDocument, revision: number): ScoresheetDocument {
  const snapshot = deepCloneDocument(document);
  snapshot.revision = revision;
  snapshot.schema_version = '1.4.0';
  return snapshot;
}

interface EditorState {
  document: ScoresheetDocument | null;
  serverRevision: number;
  template: TemplateDefinition | null;
  games: GameSummary[];
  gamesTotal: number;
  gamesPage: number;
  gamesPageSize: number;
  gamesScope: GameQueueScope;
  gamesQuery: string;
  gamesLoading: boolean;
  recognitionMode: string;
  validation: ValidationReport | null;
  recognitionRun: RecognitionRun | null;
  recognitionDiff: RecognitionDiff | null;
  recognitionState: 'idle' | 'starting' | 'running' | 'diff' | 'applied' | 'failed';
  changes: DocumentChangeLogEntry[];
  selectedField: string;
  past: ScoresheetDocument[];
  future: ScoresheetDocument[];
  dirty: boolean;
  pendingSaveSource: 'human' | 'undo' | 'redo';
  saveState: 'idle' | 'dirty' | 'saving' | 'saved' | 'conflict' | 'error';
  loading: boolean;
  error: string;
  readOnly: boolean;
  readOnlyReason: string;
  autoAcquireLease: boolean;
  online: boolean;
  leaseHolder: { username: string; surface: 'WEB' | 'MINIAPP'; expires_at: string } | null;
  seasonId: string;
  initialize: () => Promise<void>;
  loadGames: (options?: GameQueueQuery) => Promise<void>;
  openDocument: (documentId: string) => Promise<void>;
  uploadForGame: (gameId: string, file: File) => Promise<void>;
  reupload: (documentId: string, file: File) => Promise<void>;
  watchRecognition: (run: RecognitionRun, before?: ScoresheetDocument) => Promise<void>;
  recognize: () => Promise<void>;
  applyRecognition: (regions: string[]) => Promise<void>;
  clearRecognitionDiff: () => void;
  selectField: (field: string) => void;
  mutate: (mutation: (draft: ScoresheetDocument) => void) => void;
  replaceDocument: (document: ScoresheetDocument, remember?: boolean) => void;
  undo: () => void;
  redo: () => void;
  save: (source?: 'human' | 'undo' | 'redo') => Promise<void>;
  ensureSaved: () => Promise<boolean>;
  reloadAfterConflict: () => Promise<void>;
  overwriteAfterConflict: () => Promise<void>;
  validate: () => Promise<ValidationReport | null>;
  contextReviewing: boolean;
  reviewGameContext: (mappings: ScoresheetContextPlayerMapping[]) => Promise<void>;
  confirm: () => Promise<void>;
  align: (rotation: 0 | 90 | 180 | 270, corners: number[][] | null) => Promise<void>;
  reloadSource: () => Promise<void>;
  refreshChanges: () => Promise<void>;
  syncNow: () => Promise<void>;
  heartbeatLease: () => Promise<void>;
  releaseLease: () => Promise<void>;
  setOnline: (online: boolean) => void;
}

export const useEditorStore = create<EditorState>((set, get) => ({
  contextReviewing: false,
  document: null,
  serverRevision: 0,
  template: null,
  games: [],
  gamesTotal: 0,
  gamesPage: 1,
  gamesPageSize: 20,
  gamesScope: 'ACTION_REQUIRED',
  gamesQuery: '',
  gamesLoading: false,
  recognitionMode: 'automatic',
  validation: null,
  recognitionRun: null,
  recognitionDiff: null,
  recognitionState: 'idle',
  changes: [],
  selectedField: 'document',
  past: [],
  future: [],
  dirty: false,
  pendingSaveSource: 'human',
  saveState: 'idle',
  loading: true,
  error: '',
  readOnly: false,
  readOnlyReason: '',
  autoAcquireLease: true,
  online: navigator.onLine,
  leaseHolder: null,
  seasonId: '',

  initialize: async () => {
    const generation = ++documentSessionGeneration;
    set({ loading: true, error: '', autoAcquireLease: true, contextReviewing: false });
    try {
      const params = new URLSearchParams(window.location.search);
      const seasonId = params.get('season_id') ?? '';
      const requestedGameId = params.get('game_id') ?? '';
      const initialQueue: GameQueueQuery = {
        seasonId,
        scope: 'ACTION_REQUIRED',
        page: 1,
        pageSize: 20,
      };
      const [template, gamePage, requestedGame, health] = await Promise.all([
        api.template(),
        api.games(initialQueue).catch(() => ({
          items: [], total: 0, page: 1, page_size: 20, division_names: [],
        })),
        requestedGameId ? api.game(requestedGameId).catch(() => null) : Promise.resolve(null),
        api.health().catch(() => ({ status: 'ok', recognition: 'automatic', master_data: 'empty' })),
      ]);
      const games = requestedGame && !requestedGame.document_id
        && !gamePage.items.some((game) => game.id === requestedGame.id)
        ? [requestedGame, ...gamePage.items]
        : gamePage.items;
      const requestedDocumentId = requestedGame?.document_id;
      const lastId = requestedGameId ? requestedDocumentId : localStorage.getItem(LAST_DOCUMENT_KEY);
      let document: ScoresheetDocument | null = null;
      if (lastId) {
        try {
          const candidate = await api.document(lastId);
          if (isRestorableDocument(candidate)) document = candidate;
          else localStorage.removeItem(LAST_DOCUMENT_KEY);
        } catch {
          localStorage.removeItem(LAST_DOCUMENT_KEY);
        }
      }
      const recognitionRun = document ? await loadRecognitionRun(document) : null;
      const lease = document ? api.leaseState(document.id) : null;
      if (generation !== documentSessionGeneration) return;
      set({
        template,
        games,
        gamesTotal: gamePage.total,
        gamesPage: gamePage.page,
        gamesPageSize: gamePage.page_size,
        gamesScope: 'ACTION_REQUIRED',
        gamesQuery: '',
        recognitionMode: health.recognition,
        document,
        recognitionRun,
        recognitionState: document ? recognitionStateFor(document, recognitionRun) : 'idle',
        serverRevision: document?.revision ?? 0,
        changes: [],
        selectedField: document ? 'document' : '',
        past: [],
        future: [],
        dirty: false,
        pendingSaveSource: 'human',
        loading: false,
        saveState: document ? 'saved' : 'idle',
        readOnly: Boolean(document && (!lease?.token || lease.readOnly)),
        readOnlyReason: lease?.reason ?? '',
        leaseHolder: lease?.holder ?? null,
        seasonId,
      });
      if (recognitionRun && (
        activeRecognitionStatuses.has(recognitionRun.status)
        || (recognitionRun.status === 'succeeded' && !recognitionRun.auto_applied)
      )) {
        void get().watchRecognition(recognitionRun, deepCloneDocument(document!));
      }
    } catch (error) {
      if (generation !== documentSessionGeneration) return;
      set({ loading: false, error: error instanceof Error ? error.message : '加载失败' });
    }
  },

  loadGames: async (options = {}) => {
    const current = get();
    const query: GameQueueQuery = {
      seasonId: options.seasonId ?? current.seasonId,
      scope: options.scope ?? current.gamesScope,
      query: options.query ?? current.gamesQuery,
      page: options.page ?? current.gamesPage,
      pageSize: options.pageSize ?? current.gamesPageSize,
    };
    const generation = ++gameQueueGeneration;
    set({ gamesLoading: true });
    try {
      const result = await api.games(query);
      if (generation !== gameQueueGeneration) return;
      set({
        games: result.items,
        gamesTotal: result.total,
        gamesPage: result.page,
        gamesPageSize: result.page_size,
        gamesScope: query.scope ?? 'ACTION_REQUIRED',
        gamesQuery: query.query ?? '',
        gamesLoading: false,
      });
    } catch (error) {
      if (generation !== gameQueueGeneration) return;
      set({
        gamesLoading: false,
        error: error instanceof Error ? error.message : '比赛列表加载失败',
      });
    }
  },

  openDocument: async (documentId) => {
    if (!(await get().ensureSaved())) {
      throw new Error('当前草稿尚未保存，已取消切换。');
    }
    const generation = ++documentSessionGeneration;
    recognitionWatchGeneration += 1;
    const previousId = get().document?.id;
    set({ loading: true, error: '', autoAcquireLease: true });
    try {
      if (previousId && previousId !== documentId) await api.release(previousId);
      if (generation !== documentSessionGeneration) return;
      const document = await api.document(documentId);
      const recognitionRun = await loadRecognitionRun(document);
      if (generation !== documentSessionGeneration) return;
      const lease = api.leaseState(document.id);
      localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
      set({
        document,
        serverRevision: document.revision,
        validation: null,
        recognitionRun,
        recognitionDiff: null,
        recognitionState: recognitionStateFor(document, recognitionRun),
        changes: [],
        selectedField: 'document',
        past: [],
        future: [],
        dirty: false,
        pendingSaveSource: 'human',
        saveState: 'saved',
        loading: false,
        contextReviewing: false,
        readOnly: !lease?.token || Boolean(lease.readOnly),
        readOnlyReason: lease?.reason ?? '',
        leaseHolder: lease?.holder ?? null,
      });
      await get().refreshChanges();
      if (generation !== documentSessionGeneration) return;
      if (recognitionRun && (
        activeRecognitionStatuses.has(recognitionRun.status)
        || (recognitionRun.status === 'succeeded' && !recognitionRun.auto_applied)
      )) {
        void get().watchRecognition(recognitionRun, deepCloneDocument(document));
      }
    } catch (error) {
      if (generation !== documentSessionGeneration) return;
      set({
        loading: false,
        contextReviewing: false,
        error: error instanceof Error ? error.message : '打开记录表失败',
      });
      throw error;
    }
  },

  uploadForGame: async (gameId, file) => {
    if (!(await get().ensureSaved())) {
      throw new Error('当前草稿尚未保存，已取消上传。');
    }
    const generation = ++documentSessionGeneration;
    recognitionWatchGeneration += 1;
    set({ loading: true, error: '', autoAcquireLease: true });
    try {
      const { document, recognition_run: recognitionRun } = await api.createGameDocument(gameId, file);
      if (generation !== documentSessionGeneration) return;
      const lease = api.leaseState(document.id);
      localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
      set({
        document,
        serverRevision: document.revision,
        validation: null,
        changes: [],
        recognitionRun,
        recognitionDiff: null,
        recognitionState: 'running',
        selectedField: 'header',
        past: [],
        future: [],
        dirty: false,
        pendingSaveSource: 'human',
        saveState: 'saved',
        loading: false,
        contextReviewing: false,
        readOnly: !lease?.token || Boolean(lease.readOnly),
        readOnlyReason: lease?.reason ?? '',
        leaseHolder: lease?.holder ?? null,
      });
      await Promise.all([get().refreshChanges(), get().loadGames()]);
      if (generation !== documentSessionGeneration) return;
      void get().watchRecognition(recognitionRun, deepCloneDocument(document));
    } catch (error) {
      if (generation !== documentSessionGeneration) return;
      set({ loading: false, contextReviewing: false,
        error: error instanceof Error ? error.message : '上传失败' });
      throw error;
    }
  },

  reupload: async (documentId, file) => {
    if (!(await get().ensureSaved())) {
      throw new Error('当前草稿尚未保存，已取消重新上传。');
    }
    const generation = ++documentSessionGeneration;
    recognitionWatchGeneration += 1;
    set({ loading: true, error: '', autoAcquireLease: true });
    try {
      const target = get().document?.id === documentId
        ? get().document
        : await api.document(documentId);
      if (generation !== documentSessionGeneration) return;
      if (!target?.game_prior) {
        throw new Error('请先打开已绑定比赛的记录表。');
      }
      const { document, recognition_run: recognitionRun } = await api.replaceDocumentSource(
        target.id,
        target.revision,
        file,
      );
      if (generation !== documentSessionGeneration) return;
      const lease = api.leaseState(document.id);
      localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
      set({
        document,
        serverRevision: document.revision,
        validation: null,
        changes: [],
        recognitionRun,
        recognitionDiff: null,
        recognitionState: 'running',
        selectedField: 'header',
        past: [],
        future: [],
        dirty: false,
        pendingSaveSource: 'human',
        saveState: 'saved',
        loading: false,
        contextReviewing: false,
        readOnly: !lease?.token || Boolean(lease.readOnly),
        readOnlyReason: lease?.reason ?? '',
        leaseHolder: lease?.holder ?? null,
      });
      await Promise.all([get().refreshChanges(), get().loadGames()]);
      if (generation !== documentSessionGeneration) return;
      void get().watchRecognition(recognitionRun, deepCloneDocument(document));
    } catch (error) {
      if (generation !== documentSessionGeneration) return;
      set({ loading: false, contextReviewing: false,
        error: error instanceof Error ? error.message : '重新上传失败' });
      throw error;
    }
  },

  selectField: (selectedField) => set({ selectedField }),

  mutate: (mutation) => {
    if (get().contextReviewing) return;
    const {
      document: current,
      serverRevision,
      readOnly,
      readOnlyReason,
      online,
    } = get();
    if (!current) return;
    if (!online) {
      set({ error: '网络已断开；未保存输入仍保留，恢复连接并同步后才能继续修改。' });
      return;
    }
    if (readOnly) {
      set({ error: readOnlyReason || '当前工作台为只读，租约释放后将自动取得编辑权。' });
      return;
    }
    const previous = deepCloneDocument(current);
    const next = rebaseSnapshot(current, serverRevision);
    mutation(next);
    deriveScoreEvents(next);
    next.status = hasRecognitionResult(next) ? 'needs_review' : 'draft';
    set((state) => ({
      document: next,
      past: [...state.past.slice(-49), previous],
      future: [],
      dirty: true,
      pendingSaveSource: 'human',
      saveState: 'dirty',
      validation: null,
    }));
  },

  replaceDocument: (document, remember = false) => {
    documentSessionGeneration += 1;
    if (remember) localStorage.setItem(LAST_DOCUMENT_KEY, document.id);
    set({
      document,
      serverRevision: document.revision,
      dirty: false,
      pendingSaveSource: 'human',
      saveState: 'saved',
      contextReviewing: false,
    });
  },

  undo: () => {
    const { past, document, serverRevision, readOnly, online } = get();
    if (readOnly || !online || get().contextReviewing) return;
    if (!document || past.length === 0) return;
    const next = rebaseSnapshot(past[past.length - 1], serverRevision);
    set((state) => ({
      document: next,
      past: state.past.slice(0, -1),
      future: [deepCloneDocument(document), ...state.future.slice(0, 49)],
      dirty: true,
      pendingSaveSource: 'undo',
      saveState: 'dirty',
      validation: null,
    }));
  },

  redo: () => {
    const { future, document, serverRevision, readOnly, online } = get();
    if (readOnly || !online || get().contextReviewing) return;
    if (!document || future.length === 0) return;
    const next = rebaseSnapshot(future[0], serverRevision);
    set((state) => ({
      document: next,
      past: [...state.past.slice(-49), deepCloneDocument(document)],
      future: state.future.slice(1),
      dirty: true,
      pendingSaveSource: 'redo',
      saveState: 'dirty',
      validation: null,
    }));
  },

  save: async (source) => {
    if (activeSave) {
      await activeSave;
      if (get().dirty) await get().save(source);
      return;
    }
    const { document, serverRevision, pendingSaveSource, readOnly, online } = get();
    if (!online) {
      set({ error: '网络已断开；未保存输入仍保留，恢复后会先同步服务器版本。' });
      return;
    }
    if (readOnly) {
      set({ error: '当前工作台为只读，未保存输入已保留，取得编辑权后才能提交。' });
      return;
    }
    if (!document || !get().dirty) return;
    const saveSource = source ?? pendingSaveSource;
    const candidate = rebaseSnapshot(document, serverRevision);
    set({ saveState: 'saving' });
    const operation = (async () => {
      try {
        const saved = await api.save(candidate, serverRevision, saveSource);
        const current = get().document;
        if (!current || current.id !== document.id) return;
        if (current !== document) {
          set({
            document: rebaseSnapshot(current, saved.revision),
            serverRevision: saved.revision,
            dirty: true,
            saveState: 'dirty',
          });
          return;
        }
        set({
          document: saved,
          serverRevision: saved.revision,
          dirty: false,
          pendingSaveSource: 'human',
          saveState: 'saved',
        });
        await get().refreshChanges();
      } catch (error) {
        if (get().document?.id !== document.id) return;
        const status = (error as Error & { status?: number }).status;
        set({
          saveState: status === 409 ? 'conflict' : 'error',
          error: error instanceof Error ? error.message : '保存失败',
        });
      }
    })();
    activeSave = operation;
    try {
      await operation;
    } finally {
      if (activeSave === operation) activeSave = null;
    }
  },

  ensureSaved: async () => {
    if (!get().dirty) return true;
    await get().save();
    const state = get();
    const saved = !state.dirty && state.saveState !== 'conflict' && state.saveState !== 'error';
    if (!saved) set({ error: '当前草稿尚未保存，已取消切换。' });
    return saved;
  },

  reloadAfterConflict: async () => {
    const documentId = get().document?.id;
    if (!documentId || get().saveState !== 'conflict') return;
    if (!globalThis.confirm('放弃当前未保存修改，重新载入服务器上的最新内容吗？')) return;
    try {
      const latest = await api.document(documentId);
      if (get().document?.id !== documentId) return;
      set({
        document: latest,
        serverRevision: latest.revision,
        validation: null,
        past: [],
        future: [],
        dirty: false,
        pendingSaveSource: 'human',
        saveState: 'saved',
        error: '',
      });
      await get().refreshChanges();
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '重新载入失败' });
    }
  },

  overwriteAfterConflict: async () => {
    const local = get().document;
    if (!local || get().saveState !== 'conflict') return;
    if (!globalThis.confirm('以当前本地内容覆盖服务器最新草稿吗？该操作会写入人工修改记录。')) return;
    try {
      const latest = await api.document(local.id);
      if (get().document !== local) return;
      const rebased = rebaseSnapshot(local, latest.revision);
      rebased.source = structuredClone(latest.source);
      rebased.game_prior = structuredClone(latest.game_prior ?? null);
      rebased.template_id = latest.template_id;
      rebased.rules_profile = latest.rules_profile;
      rebased.recognition = structuredClone(latest.recognition ?? null);
      if (
        local.recognition &&
        rebased.recognition &&
        local.recognition.run_id === rebased.recognition.run_id
      ) {
        rebased.recognition.table_personnel = [...local.recognition.table_personnel];
        rebased.recognition.problem_paths = local.recognition.problem_paths.filter((path) =>
          rebased.recognition!.problem_paths.includes(path));
        const latestIssues = new Set(
          rebased.recognition.issues?.map((issue) => JSON.stringify(issue)) ?? [],
        );
        rebased.recognition.issues = local.recognition.issues?.filter((issue) =>
          latestIssues.has(JSON.stringify(issue)));
      }
      rebased.status = hasRecognitionResult(rebased) ? 'needs_review' : 'draft';
      rebased.acknowledged_warnings = [];
      set({
        document: rebased,
        serverRevision: latest.revision,
        dirty: true,
        pendingSaveSource: 'human',
        saveState: 'dirty',
        validation: null,
        error: '',
      });
      await get().save();
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '冲突恢复失败' });
    }
  },

  validate: async () => {
    if (!get().online || get().readOnly) {
      set({ error: get().online ? (get().readOnlyReason || '当前工作台为只读。') : '网络已断开，不能校验。' });
      return null;
    }
    let document = get().document;
    if (!document) return null;
    const generation = documentSessionGeneration;
    const documentId = document.id;
    if (get().dirty) {
      await get().save();
      if (generation !== documentSessionGeneration || get().document?.id !== documentId) return null;
      if (get().dirty || ['conflict', 'error'].includes(get().saveState)) {
        set({ error: '草稿尚未成功保存，已停止校验和提交。' });
        return null;
      }
      document = get().document;
      if (!document) return null;
    }
    const validationDocument = document;
    const validationRevision = get().serverRevision;
    try {
      const report = await api.validate(document.id, validationRevision);
      const current = get();
      if (generation !== documentSessionGeneration || current.document?.id !== documentId) return null;
      if (
        current.document !== validationDocument ||
        current.serverRevision !== validationRevision ||
        current.dirty
      ) {
        set({ error: '校验期间草稿发生了变化，旧校验结果已丢弃。' });
        return null;
      }
      set({ validation: report });
      return report;
    } catch (error) {
      if (generation !== documentSessionGeneration || get().document !== validationDocument
        || get().serverRevision !== validationRevision) return null;
      set({ error: error instanceof Error ? error.message : '校验失败' });
      return null;
    }
  },

  reviewGameContext: async (mappings) => {
    const initial = get();
    const token = initial.validation?.game_context?.review_token;
    if (!initial.document || !token || initial.contextReviewing || initial.readOnly || !initial.online) return;
    if (initial.dirty || activeSave) {
      set({ error: '草稿有新输入，请保存并重新校验后再复核。' });
      return;
    }
    const generation = documentSessionGeneration;
    const sequence = ++contextReviewSequence;
    let expectedDocument = initial.document;
    let expectedRevision = initial.serverRevision;
    const ownsReview = () => generation === documentSessionGeneration
      && sequence === contextReviewSequence;
    const matchesTarget = () => ownsReview() && get().document === expectedDocument
      && get().serverRevision === expectedRevision && !get().dirty;
    set({ contextReviewing: true, error: '' });
    try {
      const next = await api.reviewGameContext(initial.document.id, initial.serverRevision, token, mappings);
      if (!matchesTarget()) return;
      set({ document: next, serverRevision: next.revision, validation: null,
        dirty: false, saveState: 'saved', past: [], future: [] });
      expectedDocument = next;
      expectedRevision = next.revision;
      await get().validate();
      if (!matchesTarget()) return;
      await get().refreshChanges();
    } catch (error) {
      if (matchesTarget()) {
        set({ error: error instanceof Error ? error.message : '比赛信息复核失败，原图和草稿仍保留。' });
      }
    } finally {
      if (ownsReview()) set({ contextReviewing: false });
    }
  },

  confirm: async () => {
    if (!get().online || get().readOnly) {
      set({ error: get().online ? (get().readOnlyReason || '当前工作台为只读。') : '网络已断开，不能提交。' });
      return;
    }
    const report = await get().validate();
    const document = get().document;
    if (!report || !document || report.issues.some((issue) => issue.severity === 'error')) return;
    const warningCodes = report.issues
      .filter((issue) => issue.severity === 'warning')
      .map((issue) => issue.code);
    const confirmationMessage = warningCodes.length > 0
      ? `仍有 ${warningCodes.length} 类警告。确认已人工核对，并将当前记录表作为真实比赛数据提交吗？`
      : '确认将当前已保存并通过校验的记录表作为真实比赛数据提交吗？';
    if (!globalThis.confirm(confirmationMessage)) {
      return;
    }
    const confirmationDocument = document;
    const confirmationRevision = get().serverRevision;
    set({ autoAcquireLease: false });
    try {
      const confirmed = await api.confirm(document, confirmationRevision, warningCodes);
      const current = get().document;
      if (!current || current.id !== confirmationDocument.id) return;
      if (current !== confirmationDocument || get().serverRevision !== confirmationRevision) {
        const rebased = rebaseSnapshot(current, confirmed.revision);
        rebased.status = hasRecognitionResult(rebased) ? 'needs_review' : 'draft';
        rebased.acknowledged_warnings = [];
        set({
          document: rebased,
          serverRevision: confirmed.revision,
          validation: null,
          saveState: 'dirty',
          dirty: true,
          autoAcquireLease: true,
          error: '提交期间草稿发生了变化；新修改仍保留，但需要重新保存、校验并提交。',
        });
        return;
      }
      set({
        document: confirmed,
        serverRevision: confirmed.revision,
        saveState: 'saved',
        dirty: false,
        pendingSaveSource: 'human',
        readOnly: true,
        readOnlyReason: '记录表已发布；继续纠错时需要重新取得编辑权。',
        leaseHolder: null,
      });
      await Promise.all([get().refreshChanges(), get().loadGames()]);
    } catch (error) {
      set({
        autoAcquireLease: true,
        error: error instanceof Error ? error.message : '确认失败',
      });
    }
  },

  align: async (rotation, corners) => {
    if (!get().online || get().readOnly) return;
    const document = get().document;
    if (!document) return;
    const revision = get().serverRevision;
    try {
      const aligned = await api.align(document, revision, rotation, corners);
      if (get().document !== document || get().serverRevision !== revision) return;
      set({
        document: aligned,
        serverRevision: aligned.revision,
        saveState: 'saved',
        dirty: false,
        pendingSaveSource: 'human',
      });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '图片校正失败' });
    }
  },

  reloadSource: async () => {
    const current = get().document;
    if (!current) return;
    try {
      const source = await api.reloadSource(current.id);
      if (get().document !== current) return;
      set({ document: { ...current, source }, error: '' });
    } catch (error) {
      set({ error: error instanceof Error ? error.message : '重新载入原图失败' });
    }
  },

  refreshChanges: async () => {
    const document = get().document;
    const generation = documentSessionGeneration;
    if (!document) {
      set({ changes: [] });
      return;
    }
    try {
      const page = await api.changes(document.id);
      if (generation === documentSessionGeneration && get().document === document) {
        set({ changes: page.items });
      }
    } catch {
      if (generation === documentSessionGeneration && get().document === document) {
        set({ changes: [] });
      }
    }
  },

  recognize: async () => {
    if (['starting', 'running'].includes(get().recognitionState)) return;
    if (!get().online || get().readOnly) {
      set({ error: get().online ? (get().readOnlyReason || '当前工作台为只读。') : '网络已断开，不能重新识别。' });
      return;
    }
    let document = get().document;
    if (!document) {
      set({ error: '请先从比赛列表选择比赛并上传记录表照片。' });
      return;
    }
    if (!document.game_prior) {
      set({ error: '该草稿没有比赛先验，请从比赛列表重新上传。' });
      return;
    }
    if (!document.source.original_url) {
      set({ error: '请先上传记录表照片。' });
      return;
    }
    if (document.status === 'confirmed' || get().recognitionRun?.can_retry !== true
      || get().recognitionRun?.status !== 'failed') {
      set({ error: '只有未发布且识别失败的记录表可以重新识别。' });
      return;
    }
    const generation = documentSessionGeneration;
    const documentId = document.id;
    const failedRunId = get().recognitionRun!.id;
    if (!window.confirm('重新识别成功后，将用新结果覆盖整张草稿，包括已做的人工修改；再次失败则保留当前草稿。已发布后不能再识别。确定继续吗？')) return;
    // Claim the action before saving: a second click must not open another
    // confirmation or queue a second run while the first save is in flight.
    set({
      recognitionState: 'starting',
      recognitionDiff: null,
      error: '',
    });
    try {
      if (get().dirty) await get().save();
      if (generation !== documentSessionGeneration || get().document?.id !== documentId) return;
      if (get().recognitionRun?.id !== failedRunId) return;
      if (get().dirty || ['conflict', 'error'].includes(get().saveState)) {
        set({ recognitionState: 'failed' });
        return;
      }
      document = get().document;
      if (!document || document.status === 'confirmed'
        || get().recognitionRun?.status !== 'failed' || get().recognitionRun?.can_retry !== true) {
        set({ recognitionState: 'failed', error: '记录表状态已变化，请重新核对后再操作。' });
        return;
      }
      const beforeRecognition = deepCloneDocument(document);
      const run = await api.createRecognition(document.id, get().serverRevision, true);
      if (generation !== documentSessionGeneration || get().document?.id !== document.id) return;
      set({
        readOnly: true,
        readOnlyReason: '自动识别正在进行，完成前记录表保持只读。',
        leaseHolder: null,
      });
      await get().watchRecognition(run, beforeRecognition);
    } catch (error) {
      if (generation !== documentSessionGeneration || get().document?.id !== documentId) return;
      if (get().recognitionRun?.id !== failedRunId) return;
      set({
        recognitionState: 'failed',
        error: error instanceof Error ? error.message : '图像识别失败。',
      });
    }
  },

  watchRecognition: async (initialRun, before) => {
    const watchGeneration = ++recognitionWatchGeneration;
    const targetDocumentId = initialRun.document_id;
    const beforeRecognition = before
      ?? (get().document ? deepCloneDocument(get().document!) : undefined);
    let run = initialRun;
    if (
      watchGeneration !== recognitionWatchGeneration
      || get().document?.id !== targetDocumentId
    ) return;
    set({ recognitionRun: run, recognitionState: 'running', recognitionDiff: null });
    const terminalStatuses = new Set<RecognitionRun['status']>([
      'succeeded', 'failed', 'superseded', 'interrupted',
    ]);
    if (!terminalStatuses.has(run.status)) {
      try {
        run = await api.streamRecognition(run.id, (update) => {
          const state = get();
          if (
            watchGeneration === recognitionWatchGeneration
            &&
            state.document?.id === targetDocumentId
            && state.recognitionRun?.id === update.id
          ) {
            set({ recognitionRun: update, recognitionState: 'running' });
          }
        });
      } catch {
        for (let attempt = 0; attempt < RECOGNITION_POLL_LIMIT; attempt += 1) {
          if (
            watchGeneration !== recognitionWatchGeneration
            || get().document?.id !== targetDocumentId
          ) return;
          if (terminalStatuses.has(run.status)) break;
          await wait(RECOGNITION_POLL_INTERVAL_MS);
          run = await api.recognition(run.id);
          if (
            get().document?.id === targetDocumentId
            && watchGeneration === recognitionWatchGeneration
            && get().recognitionRun?.id === run.id
          ) {
            set({ recognitionRun: run, recognitionState: 'running' });
          }
        }
      }
    }
    if (
      watchGeneration !== recognitionWatchGeneration
      || get().document?.id !== targetDocumentId
      || get().recognitionRun?.id !== run.id
    ) return;
    if (run.status === 'superseded') {
      const latest = await api.latestRecognition(targetDocumentId);
      if (latest && latest.id !== run.id) {
        await get().watchRecognition(latest, beforeRecognition);
      }
      return;
    }
    if (run.status === 'failed' || run.status === 'interrupted') {
      const lease = await api.acquire(targetDocumentId).catch(() => null);
      set({
        recognitionRun: run,
        recognitionState: 'failed',
        error: run.error || '图像识别失败。',
        readOnly: !lease?.token || Boolean(lease.readOnly),
        readOnlyReason: lease?.reason ?? '',
        leaseHolder: lease?.holder ?? null,
      });
      await get().loadGames();
      return;
    }
    if (run.status !== 'succeeded') {
      set({ recognitionState: 'failed', error: '图像识别等待超时，请稍后重试。' });
      return;
    }
    if (run.auto_applied) {
      const recognized = await api.document(targetDocumentId);
      const lease = api.leaseState(targetDocumentId);
      if (
        watchGeneration !== recognitionWatchGeneration
        || get().document?.id !== targetDocumentId
        || get().recognitionRun?.id !== run.id
      ) return;
      set((state) => ({
        document: recognized,
        serverRevision: recognized.revision,
        recognitionRun: run,
        recognitionDiff: null,
        recognitionState: 'applied',
        validation: null,
        past: beforeRecognition
          ? [...state.past.slice(-49), beforeRecognition]
          : state.past,
        future: [],
        dirty: false,
        pendingSaveSource: 'human',
        saveState: 'saved',
        readOnly: !lease?.token || Boolean(lease.readOnly),
        readOnlyReason: lease?.reason ?? '',
        leaseHolder: lease?.holder ?? null,
      }));
      await Promise.all([get().refreshChanges(), get().loadGames()]);
      return;
    }
    const [diff, lease] = await Promise.all([
      api.recognitionDiff(run.id),
      api.acquire(targetDocumentId).catch(() => null),
    ]);
    if (
      watchGeneration !== recognitionWatchGeneration
      || get().document?.id !== targetDocumentId
      || get().recognitionRun?.id !== run.id
    ) return;
    set({
      recognitionRun: run,
      recognitionDiff: diff,
      recognitionState: 'diff',
      readOnly: !lease?.token || Boolean(lease.readOnly),
      readOnlyReason: lease?.reason ?? '',
      leaseHolder: lease?.holder ?? null,
    });
    await get().loadGames();
  },

  applyRecognition: async (regions) => {
    const { recognitionRun, document, readOnly, online } = get();
    if (!recognitionRun || !document) return;
    if (readOnly || !online) {
      set({ error: '当前工作台为只读，不能应用识别差异。' });
      return;
    }
    if (recognitionRun.document_id !== document.id) {
      set({ error: '当前识别结果不属于已打开的记录表，已拒绝应用。' });
      return;
    }
    const targetDocumentId = document.id;
    const targetRunId = recognitionRun.id;
    if (regions.length === 0) {
      set({ error: '请至少选择一个需要应用的识别区域。' });
      return;
    }
    if (get().dirty) await get().save();
    if (get().dirty || ['conflict', 'error'].includes(get().saveState)) return;
    if (
      get().document?.id !== targetDocumentId ||
      get().recognitionRun?.id !== targetRunId
    ) return;
    const beforeMerge = deepCloneDocument(get().document!);
    try {
      const merged = await api.applyRecognition(
        recognitionRun.id,
        get().serverRevision,
        regions,
      );
      if (
        get().document?.id !== targetDocumentId ||
        get().recognitionRun?.id !== targetRunId
      ) return;
      set((state) => ({
        document: merged,
        serverRevision: merged.revision,
        recognitionDiff: null,
        recognitionState: 'applied',
        validation: null,
        past: [...state.past.slice(-49), beforeMerge],
        future: [],
        dirty: false,
        pendingSaveSource: 'human',
        saveState: 'saved',
      }));
      await Promise.all([get().refreshChanges(), get().loadGames()]);
    } catch (error) {
      if (get().document?.id !== targetDocumentId) return;
      set({
        recognitionState: 'failed',
        error: error instanceof Error ? error.message : '应用识别结果失败。',
      });
    }
  },

  syncNow: async () => {
    if (get().contextReviewing) return;
    const document = get().document;
    if (!document) {
      set({ online: navigator.onLine });
      return;
    }
    try {
      const sync = await api.sync(document.id);
      if (get().document?.id !== document.id || get().contextReviewing) return;
      let lease = api.leaseState(document.id);
      const remoteHolder = sync.lease;
      const localHolder = lease?.holder;
      const leaseChanged = Boolean(
        lease?.token
        && (!remoteHolder || remoteHolder.client_id !== localHolder?.client_id),
      );
      if (leaseChanged) lease = await api.heartbeat(document.id);
      if (
        get().autoAcquireLease
        && (!remoteHolder || get().readOnly || !lease?.token)
        && !get().dirty
      ) {
        lease = await api.acquire(document.id);
      }
      if (get().contextReviewing || get().document?.id !== document.id) return;
      const autoAcquireLease = get().autoAcquireLease;
      set({
        online: true,
        readOnly: !autoAcquireLease || !lease?.token || Boolean(lease.readOnly),
        readOnlyReason: autoAcquireLease ? (lease?.reason ?? '') : get().readOnlyReason,
        leaseHolder: autoAcquireLease ? (lease?.holder ?? remoteHolder ?? null) : null,
      });
      if (sync.current_version !== get().serverRevision) {
        if (get().dirty) {
          set({
            saveState: 'conflict',
            error: '服务器草稿已在另一终端变化。未保存输入仍保留，请比较后选择服务器值或本地值。',
          });
          return;
        }
        const latest = await api.document(document.id);
        const recognitionRun = await loadRecognitionRun(latest);
        if (get().document?.id !== document.id || get().dirty || get().contextReviewing
          || latest.revision < get().serverRevision) return;
        set({
          document: latest,
          serverRevision: latest.revision,
          validation: null,
          recognitionRun,
          recognitionState: recognitionStateFor(latest, recognitionRun),
          saveState: 'saved',
        });
        await get().refreshChanges();
      }
    } catch {
      set({
        online: false,
        error: '与服务器的连接已中断；未保存输入仍保留，恢复后会先同步版本。',
      });
    }
  },

  heartbeatLease: async () => {
    const document = get().document;
    if (!document || !get().online) return;
    try {
      let lease = await api.heartbeat(document.id);
      if (get().autoAcquireLease && !lease?.token && !get().dirty) {
        lease = await api.acquire(document.id);
      }
      if (get().document?.id !== document.id) return;
      const autoAcquireLease = get().autoAcquireLease;
      set({
        readOnly: !autoAcquireLease || !lease?.token || Boolean(lease.readOnly),
        readOnlyReason: autoAcquireLease ? (lease?.reason ?? '') : get().readOnlyReason,
        leaseHolder: autoAcquireLease ? (lease?.holder ?? null) : null,
      });
    } catch {
      set({ readOnly: true, readOnlyReason: '编辑租约已失效。' });
    }
  },

  releaseLease: async () => {
    const documentId = get().document?.id;
    if (!documentId) return;
    set({ autoAcquireLease: false });
    await api.release(documentId);
    if (get().document?.id === documentId) {
      set({ readOnly: true, readOnlyReason: '编辑权已释放。', leaseHolder: null });
    }
  },

  setOnline: (online) => {
    if (!online) {
      set({
        online: false,
        error: '网络已断开；未保存输入仍保留，恢复后会先同步服务器版本。',
      });
      return;
    }
    void get().syncNow();
  },

  clearRecognitionDiff: () => set({
    recognitionDiff: null,
    recognitionState: get().recognitionRun ? 'applied' : 'idle',
  }),
}));
