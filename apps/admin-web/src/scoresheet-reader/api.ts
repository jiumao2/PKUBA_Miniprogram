import { createAdminClient, createIdempotencyKey, type ScoresheetMutationContext } from '@pkuba/api-client';
import { hasRecognitionResult, type ScoresheetGameContextReview, type ScoresheetContextPlayerMapping } from '@pkuba/scoresheet-domain';

import type {
  DocumentChangeLogPage,
  DocumentRecognitionResponse,
  GameDetail,
  GameQueueQuery,
  GameSummary,
  GameSummaryPage,
  RecognitionDiff,
  RecognitionRun,
  ScoresheetDocument,
  TemplateDefinition,
  ValidationIssue,
  ValidationReport,
  ValidationResult,
} from './types';

type RawLease = {
  read_only: boolean;
  read_only_reason: string;
  lease_token: string | null;
  holder: null | {
    account_id: string;
    username: string;
    client_id: string;
    surface: 'WEB' | 'MINIAPP';
    expires_at: string;
  };
};

const recognitionOperationKeys = new Map<string, string>();
const contextReviewKeys = new Map<string, string>();
type PendingPublication = { key: string; context: ScoresheetMutationContext; sourceId: string | null };
const publicationOperations = new Map<string, PendingPublication>();

function publicationOperation(id: string, revision: number) {
  return JSON.stringify([csrfToken(), clientId(), id, revision]);
}

type RawRecognition = {
  id: string;
  document_id: string;
  base_revision: number;
  source_version: number;
  cycle: number;
  trigger: string;
  model: string;
  prompt_version: string;
  image_sha256: string;
  auto_apply_allowed: boolean;
  can_retry?: boolean;
  status: string;
  attempt_count: number;
  max_attempts: number;
  next_attempt_at: string | null;
  last_error_code: string;
  last_error: string;
  recognition_notes: string;
  provider_usage: Record<string, number>;
  provider_result: Record<string, unknown> | null;
  applied_draft_version: number | null;
  created_at: string;
  updated_at: string;
};

type RawDetail = {
  id: string;
  can_upload_source: boolean;
  game: Record<string, unknown>;
  source: null | {
    id: string;
    version: number;
    url: string;
    filename: string;
    mime_type: string;
    width: number;
    height: number;
  };
  status: string;
  draft: ScoresheetDocument;
  draft_version: number;
  event_sequence: number;
  validation_report: {
    game_context?: ScoresheetGameContextReview;
    errors?: RawIssue[];
    warnings?: RawIssue[];
  };
  validation_draft_version: number | null;
  acknowledged_warnings: string[];
  recognition: RawRecognition | null;
  lease: RawLease['holder'];
  publication?: { draft_version: number; source_asset_id: string } | null;
  pending_correction?: null | {
    id: string;
    status: string;
    reason: string;
    impact_hash: string;
  };
};

type RawIssue = {
  id: string;
  severity: 'ERROR' | 'WARNING';
  code: string;
  path: string;
  paths?: string[];
  message: string;
  context?: { observed?: unknown; expected?: unknown };
};

type RawQueue = {
  game_id: string;
  can_upload_source: boolean;
  game_code: string;
  game_label: string;
  competition: string;
  division_name: string;
  venue: string;
  home_name: string;
  away_name: string;
  date: string;
  start_time: string;
  scoresheet_id: string | null;
  status: string;
};

type RawSync = {
  can_upload_source: boolean;
  current_version: number;
  current_event: number;
  requires_full_reload: boolean;
  events: Array<Record<string, unknown>>;
  lease: RawLease['holder'];
  recognition: RawRecognition | null;
};

type LeaseSession = {
  token: string | null;
  readOnly: boolean;
  reason: string;
  holder: RawLease['holder'];
  event: number;
};

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? '';
const admin = createAdminClient(baseUrl, () => window.location.assign('/'));
const leaseSessions = new Map<string, LeaseSession>();
const detailCache = new Map<string, RawDetail>();
let detailObservationSequence = 0;
const detailObservationOrders = new WeakMap<RawDetail, number>();
const runDocuments = new Map<string, string>();
const acquireFlights = new Map<string, Promise<LeaseSession>>();
const heartbeatFlights = new Map<string, Promise<LeaseSession | null>>();
const syncFlights = new Map<string, Promise<RawSync>>();
const syncDetailAnchors = new WeakMap<RawSync, RawDetail | undefined>();
const releaseFlights = new Map<string, Promise<void>>();

function clientId(): string {
  const key = 'pkuba:scoresheet-reader:web-client-id';
  const existing = sessionStorage.getItem(key);
  if (existing) return existing;
  const created = globalThis.crypto?.randomUUID?.() ?? `web-${Date.now()}-${Math.random()}`;
  sessionStorage.setItem(key, created);
  return created;
}

function leaseTokenKey(documentId: string): string {
  return `pkuba:scoresheet-reader:lease-token:${documentId}`;
}

function storedLeaseToken(documentId: string): string {
  return sessionStorage.getItem(leaseTokenKey(documentId)) ?? '';
}

export function isArchivedCorrectionConfirmed(
  documentId: string,
  search = window.location.search,
): boolean {
  return new URLSearchParams(search).get('archived_correction') === documentId;
}

function clearLease(documentId: string): void {
  sessionStorage.removeItem(leaseTokenKey(documentId));
  leaseSessions.delete(documentId);
}

function targetContext(documentId: string, revision: number) {
  const lease = leaseSessions.get(documentId);
  if (!lease?.token || lease.readOnly) {
    throw Object.assign(new Error('当前工作台为只读，正在等待编辑权自动释放。'), {
      status: 409,
      payload: { code: 'LEASE_REQUIRED' },
    });
  }
  return {
    expected_version: revision,
    lease_token: lease.token,
    client_id: clientId(),
    surface: 'WEB' as const,
  };
}

async function acquire(documentId: string): Promise<LeaseSession> {
  const inFlight = acquireFlights.get(documentId);
  if (inFlight) return inFlight;
  const request = (async () => {
    const resumeToken = leaseSessions.get(documentId)?.token ?? storedLeaseToken(documentId);
    const response = (await admin.acquireScoresheetLease(
      documentId,
      clientId(),
      'WEB',
      resumeToken,
      isArchivedCorrectionConfirmed(documentId),
    )) as unknown as RawLease;
    const session = {
      token: response.lease_token,
      readOnly: response.read_only,
      reason: response.read_only_reason,
      holder: response.holder,
      event: detailCache.get(documentId)?.event_sequence ?? 0,
    };
    if (response.lease_token && !response.read_only) {
      sessionStorage.setItem(leaseTokenKey(documentId), response.lease_token);
    } else if (response.holder?.client_id === clientId()) {
      sessionStorage.removeItem(leaseTokenKey(documentId));
    }
    leaseSessions.set(documentId, session);
    return session;
  })();
  acquireFlights.set(documentId, request);
  try {
    return await request;
  } finally {
    if (acquireFlights.get(documentId) === request) acquireFlights.delete(documentId);
  }
}

async function ensureEditable(documentId: string): Promise<LeaseSession> {
  const current = leaseSessions.get(documentId);
  if (current?.token && !current.readOnly) return current;
  return acquire(documentId);
}

function rememberDetail(rawValue: unknown, readOrder?: number): RawDetail {
  const raw = rawValue as RawDetail;
  // A GET is ordered when issued, not when its delayed response arrives. Keep
  // that order on the object so a continuation after acquiring a lease cannot
  // make an already-consumed old detail look like a new observation.
  const order = detailObservationOrders.get(raw) ?? readOrder ?? ++detailObservationSequence;
  detailObservationOrders.set(raw, order);
  const current = detailCache.get(raw.id);
  if (current && (
    order < (detailObservationOrders.get(current) ?? 0)
    || raw.draft_version < current.draft_version
    || raw.event_sequence < current.event_sequence
  )) return current;
  detailCache.set(raw.id, raw);
  return raw;
}

function documentFromDetail(rawValue: unknown): ScoresheetDocument {
  const raw = rememberDetail(rawValue);
  if (raw.recognition) runDocuments.set(raw.recognition.id, raw.id);
  const document = structuredClone(raw.draft);
  document.id = raw.id;
  document.revision = raw.draft_version;
  document.acknowledged_warnings = [...raw.acknowledged_warnings];
  const editablePublishedDraft = raw.status === 'PUBLISHED'
    && Boolean(leaseSessions.get(raw.id)?.token)
    && !leaseSessions.get(raw.id)?.readOnly;
  document.status = raw.status === 'PUBLISHED' && !editablePublishedDraft
    ? 'confirmed'
    : raw.status === 'READY'
      || (editablePublishedDraft && raw.validation_draft_version === raw.draft_version)
      ? 'validated'
      : hasRecognitionResult(document)
        ? 'needs_review'
        : 'draft';
  return document;
}

function issueFromRaw(issue: RawIssue): ValidationIssue {
  return {
    code: issue.code,
    severity: issue.severity === 'ERROR' ? 'error' : 'warning',
    paths: issue.paths?.length ? issue.paths : [issue.path],
    message: issue.message,
    observed: issue.context?.observed,
    expected: issue.context?.expected,
  };
}

function reportFromDetail(rawValue: unknown): ValidationReport {
  const raw = rememberDetail(rawValue);
  const issues = [
    ...(raw.validation_report.errors ?? []),
    ...(raw.validation_report.warnings ?? []),
  ].map(issueFromRaw);
  return {
    status: issues.some((issue) => issue.severity === 'error')
      ? 'invalid'
      : issues.some((issue) => issue.severity === 'warning')
        ? 'needs_review'
        : 'valid',
    issues,
    checked_at: new Date().toISOString(),
    game_context: raw.validation_report.game_context,
  };
}

function recognitionFromRaw(raw: RawRecognition | null): RecognitionRun | null {
  if (!raw) return null;
  runDocuments.set(raw.id, raw.document_id);
  const statusMap: Record<string, RecognitionRun['status']> = {
    QUEUED: 'pending',
    RUNNING: 'thinking',
    RETRY_WAIT: 'connecting',
    SUCCEEDED: 'succeeded',
    FAILED: 'failed',
    STOPPED: 'interrupted',
    SUPERSEDED: 'superseded',
  };
  return {
    id: raw.id,
    document_id: raw.document_id,
    base_revision: raw.base_revision,
    status: statusMap[raw.status] ?? 'failed',
    model: raw.model,
    prompt_version: raw.prompt_version,
    trigger: raw.trigger === 'MANUAL_RETRY' ? 'retry' : raw.trigger === 'REUPLOAD' ? 'reupload' : 'upload',
    source_version: raw.source_version,
    image_sha256: raw.image_sha256,
    retry_count: Math.max(0, raw.attempt_count - 1),
    attempt_count: raw.attempt_count,
    max_attempts: raw.max_attempts,
    next_attempt_at: raw.next_attempt_at,
    cached: false,
    auto_applied: raw.applied_draft_version !== null,
    can_retry: raw.can_retry === true,
    applied_revision: raw.applied_draft_version,
    recognition_notes: raw.recognition_notes,
    usage: {
      input_tokens: Number(raw.provider_usage.input_tokens ?? raw.provider_usage.prompt_tokens ?? 0),
      output_tokens: Number(raw.provider_usage.output_tokens ?? raw.provider_usage.completion_tokens ?? 0),
      image_tokens: Number(raw.provider_usage.image_tokens ?? 0),
      reasoning_tokens: Number(raw.provider_usage.reasoning_tokens ?? 0),
      total_tokens: Number(raw.provider_usage.total_tokens ?? 0),
    },
    error: raw.last_error,
    result: raw.provider_result,
    created_at: raw.created_at,
    updated_at: raw.updated_at,
  };
}

function gameFromQueue(row: RawQueue): GameSummary {
  const state: GameSummary['scoresheet_state'] = row.status === 'PUBLISHED'
    ? 'confirmed'
    : row.status === 'RECOGNITION_FAILED'
      ? 'recognition_failed'
      : ['RECOGNITION_QUEUED', 'RECOGNIZING', 'RETRY_WAIT'].includes(row.status)
        ? 'recognizing'
        : row.scoresheet_id
          ? 'recognized'
          : 'not_uploaded';
  return {
    id: row.game_id,
    competition: row.competition,
    division: row.division_name,
    date: row.date,
    scheduled_time: row.start_time,
    venue: row.venue,
    team_a_name: row.home_name,
    team_b_name: row.away_name,
    ready: Boolean(row.home_name && row.away_name),
    unavailable_reason: row.home_name && row.away_name ? '' : '比赛双方尚未落位',
    document_id: row.scoresheet_id,
    can_upload_source: row.can_upload_source === true,
    scoresheet_state: state,
  };
}

async function loadRawDetail(documentId: string): Promise<RawDetail> {
  const readOrder = ++detailObservationSequence;
  const raw = (await admin.getScoresheet(documentId)) as unknown as RawDetail;
  return rememberDetail(raw, readOrder);
}

async function loadGameSummary(gameId: string): Promise<GameSummary> {
  const page = await admin.getScoresheetQueuePage({ gameId, page: 1, pageSize: 1 });
  const game = (page.items as unknown as RawQueue[]).map(gameFromQueue)[0];
  if (!game) throw new Error('比赛不存在或当前账号无权查看。');
  return game;
}

export const api = {
  pendingCorrection(documentId: string) {
    return detailCache.get(documentId)?.pending_correction ?? null;
  },
  sourceUploadAllowed(documentId: string): boolean {
    return detailCache.get(documentId)?.can_upload_source === true;
  },

  syncIsCurrent(documentId: string, sync: RawSync): boolean {
    // Publishing advances events without changing the draft revision. A shared
    // flight must not restore permissions older than a newer detail or sync.
    const detail = detailCache.get(documentId);
    // Roles can change without a draft/event increment. Even equal watermarks
    // cannot make a flight predating the latest authoritative GET current.
    if (syncDetailAnchors.has(sync) && syncDetailAnchors.get(sync) !== detail) return false;
    return sync.current_version >= (detail?.draft_version ?? 0)
      && sync.current_event >= Math.max(detail?.event_sequence ?? 0, leaseSessions.get(documentId)?.event ?? 0);
  },

  async health(): Promise<{ status: string; recognition: string; master_data: string }> {
    const capability = await admin.getScoresheetRecognitionCapabilities();
    return {
      status: 'ok',
      recognition: capability.configured ? capability.model : 'unavailable',
      master_data: 'ready',
    };
  },

  async template(): Promise<TemplateDefinition> {
    const response = await fetch(`${baseUrl}/api/v1/scoresheets/template/definition`, {
      credentials: 'include',
    });
    if (!response.ok) throw new Error('记录表模板定义加载失败。');
    return response.json() as Promise<TemplateDefinition>;
  },

  async games(options: GameQueueQuery = {}): Promise<GameSummaryPage> {
    const page = await admin.getScoresheetQueuePage(options);
    return {
      ...page,
      items: (page.items as unknown as RawQueue[]).map(gameFromQueue),
    };
  },

  async game(id: string): Promise<GameDetail> {
    const game = await loadGameSummary(id);
    const detail = game.document_id ? documentFromDetail(await loadRawDetail(game.document_id)) : null;
    return { ...game, prior: detail?.game_prior ?? null };
  },

  async createGameDocument(gameId: string, file: File): Promise<DocumentRecognitionResponse> {
    await admin.uploadAdminGameMedia(gameId, 'SCORESHEET', true, file);
    const game = await loadGameSummary(gameId);
    if (!game?.document_id) throw new Error('记录表原图已上传，但服务端未创建共享草稿。');
    clearLease(game.document_id);
    await acquire(game.document_id);
    const raw = await loadRawDetail(game.document_id);
    const document = documentFromDetail(raw);
    const recognition = recognitionFromRaw(raw.recognition);
    if (!recognition) throw new Error('共享草稿已创建，但识别任务未入队。');
    return { document, recognition_run: recognition };
  },

  async replaceDocumentSource(
    documentId: string,
    _baseRevision: number,
    file: File,
  ): Promise<DocumentRecognitionResponse> {
    const current = await loadRawDetail(documentId);
    if (!current.can_upload_source) throw new Error('当前账号不能重新上传这场比赛的记录表原图。');
    if (!current.source) throw new Error('当前记录表没有可替换的原图。');
    await admin.replaceAdminGameMedia(current.source.id, current.source.version, true, file);
    clearLease(documentId);
    const raw = await loadRawDetail(documentId);
    const document = documentFromDetail(raw);
    const recognition = recognitionFromRaw(raw.recognition);
    if (!recognition) throw new Error('原图已替换，但识别任务未入队。');
    await acquire(documentId);
    return { document, recognition_run: recognition };
  },

  async document(id: string, isCurrent = () => true): Promise<ScoresheetDocument> {
    const raw = await loadRawDetail(id);
    if (isCurrent() && (!leaseSessions.has(id) || leaseSessions.get(id)?.readOnly)) {
      try {
        await acquire(id);
      } catch (error) {
        if (isCurrent()) {
          const latest = detailCache.get(id) ?? raw;
          leaseSessions.set(id, {
            token: null,
            readOnly: true,
            reason: error instanceof Error ? error.message : '暂时无法取得编辑权。',
            holder: latest.lease,
            event: latest.event_sequence,
          });
        }
      }
    }
    return documentFromDetail(raw);
  },

  async save(
    document: ScoresheetDocument,
    baseRevision: number,
    source: 'human' | 'undo' | 'redo' = 'human',
  ): Promise<ScoresheetDocument> {
    const lease = await ensureEditable(document.id);
    if (lease.readOnly) throw new Error('当前工作台为只读，不能保存修改。');
    const raw = (await admin.saveScoresheetDraft(
      document.id,
      targetContext(document.id, baseRevision),
      [{ path: '/', operation: 'SET', value: document }],
      {
        changeType: source === 'human' ? 'FIELD_EDIT' : source.toUpperCase(),
        explicitSave: source !== 'human',
      },
    )) as unknown as RawDetail;
    return documentFromDetail(raw);
  },

  async align(
    document: ScoresheetDocument,
    baseRevision: number,
    rotation: 0 | 90 | 180 | 270,
    corners: number[][] | null,
  ): Promise<ScoresheetDocument> {
    const next = structuredClone(document);
    next.source.rotation = rotation;
    next.source.corners = corners;
    return api.save(next, baseRevision);
  },

  async reviewGameContext(
    id: string, baseRevision: number, reviewToken: string,
    playerMappings: ScoresheetContextPlayerMapping[],
  ): Promise<ScoresheetDocument> {
    await ensureEditable(id);
    const operation = JSON.stringify([id, baseRevision, reviewToken, playerMappings]);
    const key = contextReviewKeys.get(operation) ?? createIdempotencyKey();
    contextReviewKeys.set(operation, key);
    const raw = await admin.reviewScoresheetGameContext(
      id, targetContext(id, baseRevision), reviewToken, playerMappings, key,
    );
    contextReviewKeys.delete(operation);
    return documentFromDetail(raw as unknown as RawDetail);
  },

  async validate(id: string, baseRevision: number): Promise<ValidationResult> {
    await ensureEditable(id);
    const raw = (await admin.validateScoresheet(
      id,
      targetContext(id, baseRevision),
    )) as unknown as RawDetail;
    return {
      document: documentFromDetail(raw),
      report: reportFromDetail(raw),
    };
  },

  async confirm(
    document: ScoresheetDocument,
    baseRevision: number,
    _warningCodes: string[],
  ): Promise<ScoresheetDocument> {
    const operation = publicationOperation(document.id, baseRevision);
    let pending = publicationOperations.get(operation);
    if (!pending) {
      await ensureEditable(document.id);
      let detail = detailCache.get(document.id) ?? await loadRawDetail(document.id);
      const warningIds = (detail.validation_report.warnings ?? []).map((row) => row.id);
      if (warningIds.length) {
        detail = (await admin.acknowledgeScoresheetWarnings(
          document.id,
          targetContext(document.id, baseRevision),
          warningIds,
        )) as unknown as RawDetail;
      }
      pending = { key: createIdempotencyKey(), context: targetContext(document.id, baseRevision),
        sourceId: detail.source?.id ?? null };
      publicationOperations.set(operation, pending);
    }
    let raw: RawDetail;
    try {
      raw = (await admin.publishScoresheet(
        document.id,
        pending.context,
        pending.key,
      )) as unknown as RawDetail;
    } catch (error) {
      // A lost response can follow a committed publication (which removes the
      // lease). Read its authoritative identity before asking for another write.
      let latest: RawDetail | null = null;
      try { latest = await loadRawDetail(document.id); } catch { /* Keep the original unknown-result error. */ }
      if (!latest || latest.status !== 'PUBLISHED' || latest.draft_version !== baseRevision
        || latest.publication?.draft_version !== baseRevision
        || latest.publication.source_asset_id !== pending.sourceId) throw error;
      raw = latest;
    }
    publicationOperations.delete(operation);
    clearLease(document.id);
    return documentFromDetail(raw);
  },

  hasPendingPublication(id: string, revision: number) {
    return publicationOperations.has(publicationOperation(id, revision));
  },

  async changes(id: string, limit = 50, beforeId?: number): Promise<DocumentChangeLogPage> {
    const params = new URLSearchParams({ limit: String(limit) });
    if (beforeId !== undefined) params.set('before_event', String(beforeId));
    const response = await fetch(
      `${baseUrl}/api/v1/scoresheets/${id}/changes?${params}`,
      { credentials: 'include' },
    );
    if (!response.ok) throw new Error('修改日志加载失败。');
    return response.json() as Promise<DocumentChangeLogPage>;
  },

  async createRecognition(id: string, baseRevision: number, confirmedOverwrite = false): Promise<RecognitionRun> {
    await ensureEditable(id);
    const operation = `${id}:${baseRevision}`;
    const idempotencyKey = recognitionOperationKeys.get(operation) ?? createIdempotencyKey();
    recognitionOperationKeys.set(operation, idempotencyKey);
    const response = await fetch(`${baseUrl}/api/v1/scoresheets/${id}/recognition/retry`, {
      method: 'POST',
      credentials: 'include',
      headers: {
        'Content-Type': 'application/json',
        'Idempotency-Key': idempotencyKey,
        'X-CSRFToken': csrfToken(),
      },
      body: JSON.stringify({ ...targetContext(id, baseRevision), confirmed_overwrite: confirmedOverwrite }),
    });
    if (!response.ok) throw await responseError(response);
    const recognition = recognitionFromRaw(await response.json() as RawRecognition)!;
    recognitionOperationKeys.delete(operation);
    clearLease(id);
    return recognition;
  },

  async recognition(runId: string): Promise<RecognitionRun> {
    const documentId = runDocuments.get(runId);
    if (!documentId) throw new Error('无法定位识别任务所属记录表。');
    const response = await fetch(
      `${baseUrl}/api/v1/scoresheets/${documentId}/recognition/${runId}`,
      { credentials: 'include' },
    );
    if (!response.ok) throw await responseError(response);
    return recognitionFromRaw(await response.json() as RawRecognition)!;
  },

  async latestRecognition(documentId: string): Promise<RecognitionRun | null> {
    const response = await fetch(
      `${baseUrl}/api/v1/scoresheets/${documentId}/recognition/latest`,
      { credentials: 'include' },
    );
    if (!response.ok) throw await responseError(response);
    return recognitionFromRaw(await response.json() as RawRecognition | null);
  },

  async streamRecognition(
    _runId: string,
    _onUpdate: (run: RecognitionRun) => void,
  ): Promise<RecognitionRun> {
    throw new Error('PKUBA 使用两秒增量轮询。');
  },

  async recognitionDiff(runId: string): Promise<RecognitionDiff> {
    const documentId = runDocuments.get(runId);
    if (!documentId) throw new Error('无法定位识别任务所属记录表。');
    const response = await fetch(
      `${baseUrl}/api/v1/scoresheets/${documentId}/recognition/${runId}/diff`,
      { credentials: 'include' },
    );
    if (!response.ok) throw await responseError(response);
    return response.json() as Promise<RecognitionDiff>;
  },

  async applyRecognition(
    runId: string,
    baseRevision: number,
    regions: string[],
  ): Promise<ScoresheetDocument> {
    const documentId = runDocuments.get(runId);
    if (!documentId) throw new Error('无法定位识别任务所属记录表。');
    const response = await fetch(
      `${baseUrl}/api/v1/scoresheets/${documentId}/recognition/${runId}/apply`,
      {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrfToken() },
        body: JSON.stringify({ ...targetContext(documentId, baseRevision), regions }),
      },
    );
    if (!response.ok) throw await responseError(response);
    return documentFromDetail(await response.json() as RawDetail);
  },

  async reloadSource(documentId: string): Promise<ScoresheetDocument['source']> {
    const raw = await loadRawDetail(documentId);
    return documentFromDetail(raw).source;
  },

  async acquire(documentId: string) {
    return acquire(documentId);
  },

  leaseState(documentId: string) {
    return leaseSessions.get(documentId) ?? null;
  },

  async heartbeat(documentId: string) {
    const inFlight = heartbeatFlights.get(documentId);
    if (inFlight) return inFlight;
    const request = (async () => {
      const lease = leaseSessions.get(documentId);
      if (!lease?.token || lease.readOnly) return lease ?? null;
      try {
        const response = (await admin.heartbeatScoresheetLease(
          documentId,
          lease.token,
          clientId(),
          'WEB',
        )) as unknown as RawLease;
        const next = {
          ...lease,
          holder: response.holder,
          readOnly: response.read_only,
          reason: response.read_only_reason,
        };
        leaseSessions.set(documentId, next);
        return next;
      } catch {
        sessionStorage.removeItem(leaseTokenKey(documentId));
        const next = { ...lease, token: null, readOnly: true, reason: '编辑租约已失效。' };
        leaseSessions.set(documentId, next);
        return next;
      }
    })();
    heartbeatFlights.set(documentId, request);
    try {
      return await request;
    } finally {
      if (heartbeatFlights.get(documentId) === request) heartbeatFlights.delete(documentId);
    }
  },

  async sync(documentId: string): Promise<RawSync> {
    const inFlight = syncFlights.get(documentId);
    if (inFlight) return inFlight;
    const request = (async () => {
      const detail = detailCache.get(documentId);
      const lease = leaseSessions.get(documentId);
      const response = (await admin.syncScoresheet(
        documentId,
        detail?.draft_version ?? 0,
        lease?.event ?? detail?.event_sequence ?? 0,
      )) as unknown as RawSync;
      syncDetailAnchors.set(response, detail);
      if (lease) lease.event = Math.max(lease.event, response.current_event);
      return response;
    })();
    syncFlights.set(documentId, request);
    try {
      return await request;
    } finally {
      if (syncFlights.get(documentId) === request) syncFlights.delete(documentId);
    }
  },

  async release(documentId: string): Promise<void> {
    const inFlight = releaseFlights.get(documentId);
    if (inFlight) return inFlight;
    const request = (async () => {
      const lease = leaseSessions.get(documentId);
      if (!lease?.token || lease.readOnly) {
        clearLease(documentId);
        return;
      }
      try {
        await admin.releaseScoresheetLease(documentId, lease.token, clientId(), 'WEB');
      } finally {
        clearLease(documentId);
      }
    })();
    releaseFlights.set(documentId, request);
    try {
      await request;
    } finally {
      if (releaseFlights.get(documentId) === request) releaseFlights.delete(documentId);
    }
  },
};

function csrfToken(): string {
  const match = document.cookie.match(/(?:^|; )pkuba_csrftoken=([^;]+)/);
  return match ? decodeURIComponent(match[1]) : '';
}

async function responseError(response: Response): Promise<Error> {
  const payload = await response.json().catch(() => ({ message: response.statusText }));
  return Object.assign(new Error(payload.message ?? payload.detail ?? `请求失败：${response.status}`), {
    status: response.status,
    payload,
  });
}
