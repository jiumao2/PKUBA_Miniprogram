import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import App from './App';
import { api } from './api';
import { GameBrowser } from './components/GameBrowser';
import { useEditorStore } from './store';
import { makeDocument, makeTemplate } from './test/fixtures';
import type { GameSummary } from './types';

vi.mock('./components/DocumentCanvas', () => ({ DocumentCanvas: () => null }));
vi.mock('./components/Inspector', () => ({ Inspector: () => null }));

beforeEach(() => {
  vi.restoreAllMocks();
  useEditorStore.setState({ ...useEditorStore.getInitialState(), loading: false });
  window.history.replaceState(null, '', '/scoresheet.html');
});

afterEach(() => vi.unstubAllGlobals());

function game(allowed: boolean, state: GameSummary['scoresheet_state'] = 'recognized') {
  return {
    id: 'game', competition: '北大杯', division: '男甲', date: '2026-08-28',
    scheduled_time: '18:20', venue: '五四东一', team_a_name: '数学', team_b_name: '外院',
    ready: true, unavailable_reason: '', document_id: 'sheet', scoresheet_state: state,
    can_upload_source: allowed,
  };
}

const props = {
  total: 1, page: 1, pageSize: 20, scope: 'ALL' as const, query: '', loading: false,
  onClose: vi.fn(), onLoad: vi.fn().mockResolvedValue(undefined),
  onOpen: vi.fn().mockResolvedValue(undefined), onUpload: vi.fn().mockResolvedValue(undefined),
  onReupload: vi.fn().mockResolvedValue(undefined),
};

describe('server-authoritative source permissions', () => {
  it.each(['recognized', 'confirmed'] as const)(
    'keeps an existing %s record readable without forbidden reupload actions', async (state) => {
      const onOpen = vi.fn().mockResolvedValue(undefined);
      render(<GameBrowser {...props} games={[game(false, state)]} initialGameId="game" onOpen={onOpen} />);
      expect(screen.queryByRole('button', { name: '重新上传' })).not.toBeInTheDocument();
      const upload = screen.queryByRole('button', { name: /上传并识别/ });
      if (upload) expect(upload).toBeDisabled();
      await userEvent.setup().click(screen.getByRole('button', { name: /数学.*外院/ }));
      expect(onOpen).toHaveBeenCalledExactlyOnceWith('sheet');
    },
  );

  it('retains the authorized superadmin replacement action', () => {
    render(<GameBrowser {...props} games={[game(true, 'confirmed')]} />);
    expect(screen.getByRole('button', { name: '重新上传' })).toBeEnabled();
  });

  it('cannot use a stale selected game after the server revokes upload permission', () => {
    const unuploaded = { ...game(true), document_id: null, scoresheet_state: 'not_uploaded' as const };
    const { rerender } = render(<GameBrowser {...props} games={[unuploaded]} initialGameId="game" />);
    expect(screen.getByRole('button', { name: /上传并识别/ })).toBeEnabled();
    rerender(<GameBrowser {...props} games={[{ ...unuploaded, can_upload_source: false }]} initialGameId="game" />);
    const upload = screen.queryByRole('button', { name: /上传并识别/ });
    if (upload) expect(upload).toBeDisabled();
  });

  it('rechecks the current row after a replacement file chooser was opened', async () => {
    const user = userEvent.setup();
    const reupload = vi.fn();
    const other = { ...game(true), id: 'other', document_id: null };
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const { container, rerender } = render(
      <GameBrowser {...props} games={[game(true), other]} onReupload={reupload} />,
    );
    await user.click(screen.getByRole('button', { name: '重新上传' }));
    rerender(<GameBrowser {...props} games={[game(false), other]} onReupload={reupload} />);
    await user.upload(container.querySelector('input[type="file"]')!, new File(['fixture'], 'image.jpg', { type: 'image/jpeg' }));
    expect(reupload).not.toHaveBeenCalled();
  });

  it('does not expose a replacement when an older response omits the capability', () => {
    const legacy = game(false) as Partial<GameSummary>;
    delete legacy.can_upload_source;
    render(<GameBrowser {...props} games={[legacy as GameSummary]} />);
    expect(screen.queryByRole('button', { name: '重新上传' })).not.toBeInTheDocument();
  });

  it('wires the source pane to server permission without disabling photo viewing', async () => {
    const document = makeDocument('readonly-source');
    document.source.original_url = '/fixture.jpg';
    const reload = vi.fn().mockResolvedValue(undefined);
    useEditorStore.setState({ document, template: makeTemplate(), readOnly: true, canUploadSource: false,
      initialize: vi.fn(), refreshChanges: vi.fn(), releaseLease: vi.fn(), reloadSource: reload });
    render(<App />);
    expect(screen.queryByRole('button', { name: '重新上传照片' })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: '放大原图' }));
    expect(screen.getByRole('button', { name: '原图倍率复位' })).toHaveTextContent('110%');
    await userEvent.setup().click(screen.getByRole('button', { name: '重新载入原图' }));
    expect(reload).toHaveBeenCalledOnce();
    act(() => useEditorStore.setState({ canUploadSource: true }));
    expect(screen.getByRole('button', { name: '重新上传照片' })).toBeEnabled();
    act(() => useEditorStore.setState({ canUploadSource: false }));
    expect(screen.queryByRole('button', { name: '重新上传照片' })).not.toBeInTheDocument();
  });
});

describe('source capability transport and synchronization', () => {
  it('uses queue capability instead of document status', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(Response.json({
      items: [false, true].map((allowed, index) => ({
        game_id: `queue-${index}`, competition: '北大杯', division_name: '男甲', date: '2026-08-28',
        start_time: '18:20', venue: '五四东一', home_name: '数学', away_name: '外院',
        scoresheet_id: `sheet-${index}`, status: 'DRAFT', can_upload_source: allowed,
      })), total: 2, page: 1, page_size: 20, division_names: [],
    })));
    const page = await api.games();
    expect(page.items.map(item => item.can_upload_source)).toEqual([false, true]);
    expect(page.items.map(item => item.scoresheet_state)).toEqual(['recognized', 'recognized']);
  });

  it('rechecks detail before multipart upload and keeps permission outside the draft', async () => {
    const draft = makeDocument('transport-readonly');
    const raw = { id: draft.id, status: 'DRAFT', draft, draft_version: 3, event_sequence: 5,
      acknowledged_warnings: [], recognition: null, can_upload_source: false,
      source: { id: 'asset', version: 2 }, lease: null };
    const fetcher = vi.fn(async (url: string) => Response.json(String(url).endsWith('/lease')
      ? { read_only: true, read_only_reason: '已发布记录只读', lease_token: null, holder: null }
      : raw));
    vi.stubGlobal('fetch', fetcher);
    const document = await api.document(draft.id);
    expect(api.sourceUploadAllowed(draft.id)).toBe(false);
    expect(document).not.toHaveProperty('can_upload_source');
    fetcher.mockClear();
    await expect(api.replaceDocumentSource(draft.id, 3, new File(['fixture'], 'source.jpg')))
      .rejects.toThrow('不能重新上传');
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(String(fetcher.mock.calls[0][0])).toContain(draft.id);
  });

  it('updates the source pane on sync without replacing unsaved draft content', async () => {
    const document = makeDocument('sync-permission');
    document.header.crew_chief = '保留人工输入';
    useEditorStore.setState({ document, serverRevision: document.revision,
      dirty: true, canUploadSource: true, online: true });
    vi.spyOn(api, 'leaseState').mockReturnValue(null);
    vi.spyOn(api, 'sync').mockResolvedValue({ current_version: document.revision,
      current_event: 1, requires_full_reload: false, events: [], lease: null,
      recognition: null, can_upload_source: false });
    await useEditorStore.getState().syncNow();
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    expect(useEditorStore.getState().document).toBe(document);
    expect(useEditorStore.getState().dirty).toBe(true);
  });

  it('does not restore an old permission after switching A to B and back to A', async () => {
    const first = makeDocument('permission-a');
    const second = makeDocument('permission-b');
    useEditorStore.setState({ document: first, serverRevision: 0, canUploadSource: true });
    let resolve!: (response: Awaited<ReturnType<typeof api.sync>>) => void;
    vi.spyOn(api, 'sync').mockImplementation(() => new Promise(done => { resolve = done; }));
    vi.spyOn(api, 'document').mockImplementation(async (id) => id === first.id ? first : second);
    vi.spyOn(api, 'latestRecognition').mockResolvedValue(null);
    vi.spyOn(api, 'release').mockResolvedValue(undefined);
    vi.spyOn(api, 'changes').mockResolvedValue({ items: [], next_before_id: null });
    vi.spyOn(api, 'leaseState').mockReturnValue(null);
    vi.spyOn(api, 'sourceUploadAllowed').mockReturnValue(false);
    const old = useEditorStore.getState().syncNow();
    await useEditorStore.getState().openDocument(second.id);
    await useEditorStore.getState().openDocument(first.id);
    resolve({ current_version: 0, current_event: 1, requires_full_reload: false,
      events: [], lease: null, recognition: null, can_upload_source: true });
    await old;
    expect(useEditorStore.getState().document?.id).toBe(first.id);
    expect(useEditorStore.getState().canUploadSource).toBe(false);
  });
});

describe('same-record capability ordering', () => {
  let sequence = 0;

  function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: Error) => void;
    const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
  }

  async function setupPendingSync(keepPermission = false) {
    const id = `ordered-capability-${++sequence}`;
    const document = makeDocument(id);
    document.revision = 6;
    document.source.original_url = '/fixture.jpg';
    const raw = {
      id, status: 'READY', draft: document, draft_version: 6, event_sequence: 10,
      validation_report: { errors: [], warnings: [] }, validation_draft_version: 6,
      acknowledged_warnings: [], recognition: null, publication: null as null | { publication_number: number },
      can_upload_source: true, lease: null,
      source: { id: 'ordered-source', version: 1, url: '/fixture.jpg', filename: 'fixture.jpg', mime_type: 'image/jpeg', width: 100, height: 100 },
    };
    const second = { ...structuredClone(raw), id: `${id}-other`, can_upload_source: false };
    second.draft.id = second.id;
    const records = new Map([[id, raw], [second.id, second]]);
    const pending = deferred<Response>();
    let syncRequests = 0;
    let publications = 0;
    const response = (value: unknown) => Response.json(value);
    const syncValue = (allowed: boolean, event: number) => ({
      current_version: 6, current_event: event, requires_full_reload: false,
      events: [], lease: api.leaseState(id)?.holder ?? null, recognition: null, can_upload_source: allowed,
    });
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, options?: RequestInit) => {
      const match = new URL(String(input), 'http://fixture.invalid').pathname.match(/^\/api\/v1\/scoresheets\/([^/]+)(?:\/(.*))?$/);
      const record = match && records.get(match[1]);
      if (!record) throw new Error('Unexpected test request');
      switch (match?.[2] ?? 'detail') {
        case 'detail': return response(record);
        case 'lease': {
          const body = JSON.parse(String(options?.body ?? '{}')) as { client_id: string };
          return response({ read_only: !record.can_upload_source, read_only_reason: '',
            lease_token: record.can_upload_source ? 'synthetic-test-lease' : null,
            holder: record.can_upload_source ? { client_id: body.client_id, surface: 'WEB', username: '测试管理员' } : null });
        }
        case 'lease/release': return new Response(null, { status: 204 });
        case 'recognition/latest': return response(null);
        case 'validate': return response(record);
        case 'publish':
          publications++;
          record.status = 'PUBLISHED';
          record.publication = { publication_number: 1 };
          record.can_upload_source = keepPermission;
          record.event_sequence = 11;
          return response(record);
        case 'sync':
          syncRequests++;
          return syncRequests === 1 ? pending.promise : response(syncValue(record.can_upload_source, record.event_sequence));
        default: throw new Error('Unexpected test request');
      }
    }));
    vi.spyOn(api, 'changes').mockResolvedValue({ items: [], next_before_id: null });
    vi.spyOn(api, 'games').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, division_names: [] });
    vi.spyOn(window, 'confirm').mockReturnValue(true);
    const loaded = await api.document(id);
    useEditorStore.setState({ ...useEditorStore.getInitialState(), document: loaded, template: makeTemplate(),
      serverRevision: 6, loading: false, dirty: false, saveState: 'saved', online: true,
      readOnly: false, canUploadSource: true, initialize: vi.fn(), releaseLease: vi.fn() });
    render(<App />);
    const oldPayload = syncValue(true, 10);
    let completion!: Promise<void>;
    await act(async () => { completion = useEditorStore.getState().syncNow(); await Promise.resolve(); });
    return { id, raw, second, pending, completion, oldPayload, syncRequests: () => syncRequests, publications: () => publications };
  }

  it('keeps a known publication denial when an older same-revision sync arrives', async () => {
    const fixture = await setupPendingSync();
    await act(async () => { await useEditorStore.getState().confirm(); });
    const published = useEditorStore.getState().document;
    expect(useEditorStore.getState().serverRevision).toBe(6);
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    await act(async () => { fixture.pending.resolve(Response.json(fixture.oldPayload)); await fixture.completion; });
    expect(useEditorStore.getState().document).toBe(published);
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    expect(screen.queryByRole('button', { name: '重新上传照片' })).not.toBeInTheDocument();
    expect(useEditorStore.getState().readOnly).toBe(true);
    expect(fixture.publications()).toBe(1);
  });

  it.each([10, 11])('does not apply a shared old sync flight after reopening A at event %i', async (event) => {
    const fixture = await setupPendingSync();
    fixture.raw.can_upload_source = false;
    fixture.raw.event_sequence = event;
    await act(async () => {
      await useEditorStore.getState().openDocument(fixture.second.id);
      await useEditorStore.getState().openDocument(fixture.id);
    });
    const reopened = useEditorStore.getState().document;
    let joined!: Promise<void>;
    await act(async () => { joined = useEditorStore.getState().syncNow(); await Promise.resolve(); });
    await act(async () => { fixture.pending.resolve(Response.json(fixture.oldPayload)); await Promise.all([fixture.completion, joined]); });
    expect(useEditorStore.getState().document).toBe(reopened);
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    expect(screen.queryByRole('button', { name: '重新上传照片' })).not.toBeInTheDocument();
    await act(async () => { await useEditorStore.getState().syncNow(); });
    expect(fixture.syncRequests()).toBeGreaterThanOrEqual(2);
    expect(useEditorStore.getState().canUploadSource).toBe(false);
  });

  it('does not replace a successful publication state with an older sync error', async () => {
    const fixture = await setupPendingSync();
    await act(async () => { await useEditorStore.getState().confirm(); });
    const error = useEditorStore.getState().error;
    await act(async () => { fixture.pending.reject(new Error('Delayed network failure')); await fixture.completion; });
    expect(useEditorStore.getState().error).toBe(error);
    expect(useEditorStore.getState().online).toBe(true);
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    expect(fixture.publications()).toBe(1);
  });

  it('keeps the superadmin capability from a current post-publication sync', async () => {
    const fixture = await setupPendingSync(true);
    await act(async () => { await useEditorStore.getState().confirm(); });
    await act(async () => { fixture.pending.resolve(Response.json(fixture.oldPayload)); await fixture.completion; });
    await act(async () => { await useEditorStore.getState().syncNow(); });
    expect(useEditorStore.getState().canUploadSource).toBe(true);
    expect(screen.getByRole('button', { name: '重新上传照片' })).toBeEnabled();
    expect(fixture.publications()).toBe(1);
  });

  it('uses the latest source capability in a cached game queue without a manual refresh', async () => {
    const fixture = await setupPendingSync();
    useEditorStore.setState({ games: [{ ...game(true), document_id: fixture.id }], gamesTotal: 1 });
    fixture.raw.can_upload_source = false;
    fixture.raw.event_sequence = 11;
    await act(async () => {
      fixture.pending.resolve(Response.json({ ...fixture.oldPayload, can_upload_source: false, current_event: 11, lease: null }));
      await fixture.completion;
    });
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    expect(screen.queryByRole('button', { name: '重新上传照片' })).not.toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole('button', { name: '选择比赛' }));
    expect(screen.getByRole('dialog', { name: '选择比赛' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '重新上传' })).not.toBeInTheDocument();
    expect(api.games).not.toHaveBeenCalled();
    // The denial also survives switching away from A: it is not only a visual
    // override for whichever record happens to be open.
    expect(useEditorStore.getState().games[0].can_upload_source).toBe(false);
    await act(async () => { await useEditorStore.getState().openDocument(fixture.second.id); });
    expect(screen.queryByRole('button', { name: '重新上传' })).not.toBeInTheDocument();
    act(() => useEditorStore.setState({ games: [
      ...useEditorStore.getState().games,
      { ...game(true), id: 'game-c', document_id: 'record-c' },
      { ...game(false), id: 'game-d', document_id: 'record-d' },
    ] }));
    expect(useEditorStore.getState().games.map(row => row.can_upload_source)).toEqual([false, true, false]);
    expect(screen.getAllByRole('button', { name: '重新上传' })).toHaveLength(1);
  });
});

describe('detail reads and lease continuations', () => {
  let sequence = 0;
  const deferred = <T,>() => {
    let resolve!: (value: T) => void;
    let reject!: (reason: Error) => void;
    const promise = new Promise<T>((done, fail) => { resolve = done; reject = fail; });
    return { promise, resolve, reject };
  };

  async function fixture() {
    const id = `detail-lease-order-${++sequence}`;
    const draft = makeDocument(id);
    draft.revision = 6;
    draft.source.original_url = '/source-1.jpg';
    draft.source.version = 1;
    const raw = {
      id, status: 'READY', draft, draft_version: 6, event_sequence: 10,
      can_upload_source: true, source: { id: 'source-1', version: 1, url: '/source-1.jpg' },
      validation_report: { errors: [], warnings: [] }, validation_draft_version: 6,
      acknowledged_warnings: [], recognition: null, lease: null,
    };
    const second = { ...structuredClone(raw), id: `${id}-b` };
    second.draft.id = second.id;
    const records = new Map([[id, raw], [second.id, second]]);
    const handlers = new Map<string, () => Promise<Response>>();
    const calls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (input: string | URL | Request, options?: RequestInit) => {
      const match = new URL(String(input), 'http://fixture.invalid').pathname.match(/^\/api\/v1\/scoresheets\/([^/]+)(?:\/(.*))?$/);
      const record = match && records.get(match[1]);
      if (!record) throw new Error('Unexpected fixture request');
      const operation = match?.[2] ?? 'detail';
      const key = `${record.id}:${operation}`;
      calls.push(key);
      if (handlers.has(key)) return handlers.get(key)!();
      if (operation === 'detail') return Response.json(record);
      if (operation === 'lease') {
        const body = JSON.parse(String(options?.body ?? '{}')) as { client_id: string };
        return Response.json({ read_only: !record.can_upload_source, read_only_reason: '',
          lease_token: record.can_upload_source ? 'synthetic-context-lease' : null,
          holder: record.can_upload_source ? { client_id: body.client_id, username: '测试管理员', surface: 'WEB' } : null });
      }
      if (operation === 'lease/release') return new Response(null, { status: 204 });
      if (operation === 'recognition/latest') return Response.json(null);
      if (operation === 'changes') return Response.json({ items: [], next_before_id: null });
      if (operation === 'sync') return Response.json({
        current_version: record.draft_version, current_event: record.event_sequence,
        can_upload_source: record.can_upload_source, requires_full_reload: false,
        lease: null, recognition: null, events: [],
      });
      throw new Error(`Unexpected fixture operation ${operation}`);
    }));
    const document = await api.document(id);
    useEditorStore.setState({ ...useEditorStore.getInitialState(), document, serverRevision: 6,
      canUploadSource: true, readOnly: false, loading: false, online: true, autoAcquireLease: true,
      games: [{ ...game(true), id: `${id}-game`, document_id: id },
        { ...game(true), id: `${id}-game-b`, document_id: second.id },
        { ...game(true), id: `${id}-game-c`, document_id: `${id}-c` },
        { ...game(false), id: `${id}-game-d`, document_id: `${id}-d` }] });
    return { id, raw, second, handlers, calls };
  }

  it.each([
    ['sync', 'readonly'], ['sync', 'failure'], ['heartbeat', 'readonly'], ['heartbeat', 'failure'],
  ] as const)('does not acquire A after switching to B during %s %s', async (kind, outcome) => {
    const f = await fixture();
    const pending = deferred<Response>();
    f.handlers.set(`${f.id}:lease/heartbeat`, () => pending.promise);
    const completion = kind === 'sync'
      ? useEditorStore.getState().syncNow() : useEditorStore.getState().heartbeatLease();
    await vi.waitFor(() => expect(f.calls).toContain(`${f.id}:lease/heartbeat`));
    await useEditorStore.getState().openDocument(f.second.id);
    const current = useEditorStore.getState().document;
    const start = f.calls.length;
    if (outcome === 'readonly') pending.resolve(Response.json({ read_only: true, read_only_reason: '旧租约已失效', holder: null }));
    else pending.reject(new Error('Synthetic delayed heartbeat failure'));
    await completion;
    expect(f.calls.slice(start)).not.toContain(`${f.id}:lease`);
    expect(useEditorStore.getState().document).toBe(current);
    expect(useEditorStore.getState().readOnly).toBe(false);
    expect(useEditorStore.getState().canUploadSource).toBe(true);
  });

  it.each(['sync', 'heartbeat'] as const)('retains current authorized lease recovery for %s', async (kind) => {
    const f = await fixture();
    f.handlers.set(`${f.id}:lease/heartbeat`, async () => { throw new Error('Synthetic expired lease'); });
    const start = f.calls.length;
    await (kind === 'sync' ? useEditorStore.getState().syncNow() : useEditorStore.getState().heartbeatLease());
    expect(f.calls.slice(start)).toContain(`${f.id}:lease`);
    expect(useEditorStore.getState().document?.id).toBe(f.id);
    expect(useEditorStore.getState().readOnly).toBe(false);
  });

  it('updates only the fetched record in the cached queue before any poll', async () => {
    const f = await fixture();
    await useEditorStore.getState().openDocument(f.second.id);
    f.raw.can_upload_source = false;
    await useEditorStore.getState().openDocument(f.id);
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    await useEditorStore.getState().openDocument(f.second.id);
    const rows = useEditorStore.getState().games;
    expect(rows.map(row => row.can_upload_source)).toEqual([false, true, true, false]);
    render(<GameBrowser {...props} games={rows} />);
    expect(screen.getAllByRole('button', { name: '重新上传' })).toHaveLength(2);
    expect(f.calls.some(call => call.endsWith(':sync'))).toBe(false);
  });

  it('does not acquire A when its opening GET completes after B was opened', async () => {
    const f = await fixture();
    const pending = deferred<Response>();
    const start = f.calls.length;
    f.handlers.set(`${f.id}:detail`, () => pending.promise);
    const opening = useEditorStore.getState().openDocument(f.id);
    await vi.waitFor(() => expect(f.calls.slice(start)).toContain(`${f.id}:detail`));
    await useEditorStore.getState().openDocument(f.second.id);
    const current = useEditorStore.getState().document;
    const afterNavigation = f.calls.length;
    pending.resolve(Response.json(f.raw));
    await opening;
    expect(f.calls.slice(afterNavigation)).not.toContain(`${f.id}:lease`);
    expect(useEditorStore.getState().document).toBe(current);
  });

  it('uses a freshly read permission for the initial cached queue', async () => {
    const f = await fixture();
    f.raw.can_upload_source = false;
    f.raw.draft.game_prior = {
      game_id: `${f.id}-game`, competition: '北大杯', division: '男甲', date: '2026-08-28',
      scheduled_time: '18:20', venue: '五四东一', source_hash: 'synthetic-fixture', locked_paths: [],
      team_a: { team_id: 'team-a', name: '数学', player_names: [] },
      team_b: { team_id: 'team-b', name: '外院', player_names: [] },
    };
    localStorage.setItem('scoresheet-reader:last-document-id', f.id);
    vi.spyOn(api, 'template').mockResolvedValue(makeTemplate());
    vi.spyOn(api, 'health').mockResolvedValue({ status: 'ok', recognition: 'unavailable', master_data: 'ready' });
    vi.spyOn(api, 'games').mockResolvedValue({ items: useEditorStore.getState().games,
      total: 4, page: 1, page_size: 20, division_names: [] });
    await useEditorStore.getState().initialize();
    expect(useEditorStore.getState().document?.id).toBe(f.id);
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    expect(useEditorStore.getState().games.map(row => row.can_upload_source)).toEqual([false, true, true, false]);
  });

  it('applies reload-source permission to the current pane and only its cached row', async () => {
    const f = await fixture();
    f.raw.can_upload_source = false;
    useEditorStore.getState().mutate(document => { document.header.crew_chief = '保留人工填写'; });
    await useEditorStore.getState().reloadSource();
    expect(useEditorStore.getState().canUploadSource).toBe(false);
    expect(useEditorStore.getState().games.map(row => row.can_upload_source)).toEqual([false, true, true, false]);
    expect(useEditorStore.getState().dirty).toBe(true);
    expect(useEditorStore.getState().document?.header.crew_chief).toBe('保留人工填写');
  });

  function revoke(f: Awaited<ReturnType<typeof fixture>>, newer: boolean) {
    f.raw.can_upload_source = false;
    if (newer) {
      f.raw.draft_version = 7; f.raw.event_sequence = 12;
      f.raw.source.id = 'source-2'; f.raw.source.version = 2;
      f.raw.draft.source.version = 2; f.raw.draft.source.original_url = '/source-2.jpg';
    }
  }

  it.each([false, true])('does not restore an old detail after acquire (new source %s)', async (newer) => {
    const f = await fixture();
    await api.release(f.id);
    const pending = deferred<Response>();
    const start = f.calls.length;
    f.handlers.set(`${f.id}:lease`, () => pending.promise);
    const old = api.document(f.id);
    await vi.waitFor(() => expect(f.calls.slice(start)).toContain(`${f.id}:lease`));
    revoke(f, newer);
    const currentSource = await api.reloadSource(f.id);
    expect(api.sourceUploadAllowed(f.id)).toBe(false);
    pending.resolve(Response.json({ read_only: true, read_only_reason: '', lease_token: null, holder: null }));
    const returned = await old;
    expect(api.sourceUploadAllowed(f.id)).toBe(false);
    expect(returned.revision).toBe(f.raw.draft_version);
    expect(returned.source.version).toBe(currentSource.version);
  });

  it.each([false, true])('ignores a late older GET and allows a fresh same-watermark grant (new source %s)', async (newer) => {
    const f = await fixture();
    const captured = structuredClone(f.raw);
    const pending = deferred<Response>();
    let reads = 0;
    const start = f.calls.length;
    f.handlers.set(`${f.id}:detail`, () => ++reads === 1 ? pending.promise : Promise.resolve(Response.json(f.raw)));
    const old = api.reloadSource(f.id);
    await vi.waitFor(() => expect(reads).toBe(1));
    revoke(f, newer);
    const latest = await api.reloadSource(f.id);
    expect(api.sourceUploadAllowed(f.id)).toBe(false);
    pending.resolve(Response.json(captured));
    expect((await old).version).toBe(latest.version);
    expect(api.sourceUploadAllowed(f.id)).toBe(false);
    f.raw.can_upload_source = true;
    await api.reloadSource(f.id);
    expect(api.sourceUploadAllowed(f.id)).toBe(true);
    expect(f.calls.slice(start).every(call => call === `${f.id}:detail`)).toBe(true);
  });

  it('accepts the newer GET even when the older GET finishes first at equal watermarks', async () => {
    const f = await fixture();
    const pendingOld = deferred<Response>(), pendingNew = deferred<Response>();
    let reads = 0;
    f.handlers.set(`${f.id}:detail`, () => ++reads === 1 ? pendingOld.promise : pendingNew.promise);
    const old = api.reloadSource(f.id), current = api.reloadSource(f.id);
    pendingOld.resolve(Response.json(f.raw));
    await old;
    expect(api.sourceUploadAllowed(f.id)).toBe(true);
    pendingNew.resolve(Response.json({ ...f.raw, can_upload_source: false }));
    await current;
    expect(api.sourceUploadAllowed(f.id)).toBe(false);
  });
});
