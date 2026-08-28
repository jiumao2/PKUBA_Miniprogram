import { beforeEach, describe, it, expect, vi } from 'vitest';
import { makeDocument } from './test/fixtures';
import { response } from './test/operationTransport';
// Real store -> real Web API -> shared admin client -> mock fetch. No database.
let raw: any, holder: any, lease: string, leaseSequence: number, mode: string;
let calls: Array<{ route: string; body: any; key: string | null }>, publicationCount: number;
let api: any, store: any;
const id = 'synthetic-web-sheet';
const clone = <T,>(v: T): T => structuredClone(v);
const publishCalls = () => calls.filter(c => c.route === 'publish');
beforeEach(async () => {
  vi.resetModules(); sessionStorage.clear(); localStorage.clear();
  sessionStorage.setItem('pkuba:scoresheet-reader:web-client-id', 'synthetic-web-client');
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  calls = []; publicationCount = 0; holder = null; lease = ''; leaseSequence = 0; mode = 'loss-only';
  const draft = makeDocument(id); draft.revision = 6; draft.status = 'validated';
  raw = { id, game: {}, source: { id: 'synthetic-media', version: 1, url: '/synthetic.png', filename: 'synthetic.png', mime_type: 'image/png', width: 100, height: 100 },
    draft, draft_version: 6, event_sequence: 10, status: 'READY', validation_report: { errors: [], warnings: [] }, validation_draft_version: 6, acknowledged_warnings: [], recognition: null, publication: null, lease: null };
  vi.stubGlobal('fetch', vi.fn(async (input: any, options: any = {}) => {
    const path = new URL(String(input), 'http://synthetic.invalid').pathname;
    const route = path === `/api/v1/scoresheets/${id}` ? 'detail' : path.replace(`/api/v1/scoresheets/${id}/`, '');
    const body = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    calls.push({ route, body, key: new Headers(options.headers).get('Idempotency-Key') });
    if (route === 'detail') return response(raw);
    if (route === 'lease') {
      lease = `synthetic-lease-${++leaseSequence}`; holder = { account_id: 'synthetic-account', username: '合成超管', client_id: 'synthetic-web-client', surface: 'WEB', expires_at: '2099-01-01T00:00:00Z' };
      return response({ read_only: false, read_only_reason: '', lease_token: lease, holder });
    }
    if (route === 'lease/heartbeat') return holder ? response({ read_only: false, read_only_reason: '', lease_token: lease, holder }) : response({ code: 'LEASE_REQUIRED', message: '模拟：编辑租约已失效。' }, 409);
    if (route === 'validate') {
      if (!holder || body.lease_token !== lease) return response({ code: 'LEASE_REQUIRED', message: '模拟：编辑租约已失效。' }, 409);
      raw.status = 'READY'; raw.validation_draft_version = raw.draft_version; raw.event_sequence++;
      return response(raw);
    }
    if (route === 'publish') {
      if (mode === 'loss-only') throw new TypeError('SYNTHETIC_RESPONSE_LOSS');
      publicationCount++; raw.status = 'PUBLISHED'; raw.publication = { id: 'synthetic-publication', publication_number: publicationCount, draft_version: raw.draft_version, source_asset_id: raw.source.id }; raw.event_sequence++; holder = null; lease = '';
      if (mode === 'commit-loss') throw new TypeError('SYNTHETIC_RESPONSE_LOSS');
      return response(raw);
    }
    if (route === 'sync') return response({ current_version: raw.draft_version, current_event: raw.event_sequence, requires_full_reload: false, events: [{ event_sequence: raw.event_sequence, type: 'PUBLISHED', payload: { publication_id: 'synthetic-publication', publication_number: publicationCount } }], lease: holder, recognition: null });
    throw new Error(`UNEXPECTED_MOCK_ROUTE ${route}`);
  }));
  ({ api } = await import('./api'));
  ({ useEditorStore: store } = await import('./store'));
  vi.spyOn(api, 'changes').mockResolvedValue({ items: [], next_before_id: null });
  vi.spyOn(api, 'games').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, division_names: [] });
  await api.acquire(id);
  store.setState({ document: clone(draft), serverRevision: 6, template: null, games: [], gamesTotal: 0, gamesPage: 1, gamesPageSize: 20, gamesScope: 'ACTION_REQUIRED', gamesQuery: '', gamesLoading: false, recognitionMode: 'mock', validation: null,
    recognitionRun: null, recognitionDiff: null, recognitionState: 'idle', changes: [], selectedField: 'document', past: [], future: [], dirty: false, pendingSaveSource: 'human', saveState: 'saved', loading: false, error: '', readOnly: false, readOnlyReason: '', autoAcquireLease: true, online: true, leaseHolder: null, seasonId: '', contextReviewing: false });
});
describe('real Web store publish call chain', () => {
  it('known success becomes confirmed/read-only and does not auto-republish on sync', async () => {
    mode = 'success'; await store.getState().confirm();
    expect(publishCalls().length).toBe(1); expect(store.getState().document.status).toBe('confirmed');
    expect(store.getState().readOnly).toBe(true); expect(store.getState().error).toBe('');
    await store.getState().syncNow(); expect(publishCalls().length).toBe(1);
  });
  it('same unchanged publish intent after response loss keeps its key', async () => {
    await store.getState().confirm(); await store.getState().confirm();
    const writes = publishCalls(); expect(writes.length).toBe(2);
    expect(JSON.stringify(writes[0].body) === JSON.stringify(writes[1].body)).toBe(true);
    expect(writes[1].key === writes[0].key).toBe(true);
  });
  it('committed-response-loss resolves through authoritative publication without stale-lease validation', async () => {
    mode = 'commit-loss'; await store.getState().confirm(); expect(publicationCount).toBe(1);
    expect(store.getState().error).toBe('');
    expect(publishCalls().length).toBe(1);
    expect(store.getState().document.status).toBe('confirmed');
    expect(store.getState().readOnly).toBe(true);
  });
  it('recovered publication stays confirmed through event-only sync without another publish', async () => {
    mode = 'commit-loss'; await store.getState().confirm();
    await store.getState().syncNow();
    expect(publicationCount).toBe(1); expect(publishCalls().length).toBe(1);
    expect(store.getState().document.status).toBe('confirmed');
    expect(store.getState().error).toBe('');
  });
});
