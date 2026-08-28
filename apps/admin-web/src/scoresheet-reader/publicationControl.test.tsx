import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { makeDocument, makeTemplate } from './test/fixtures';
import { response } from './test/operationTransport';

vi.mock('./components/DocumentCanvas', () => ({ DocumentCanvas: () => null }));
vi.mock('./components/Inspector', () => ({ Inspector: () => null }));
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

let api: typeof import('./api').api;
let store: typeof import('./store').useEditorStore;
let App: typeof import('./App').default;
let publicationCount: number;
let detailAvailable: boolean;
let leaseActive: boolean;
let original: { key: string | null; body: string } | undefined;
let writes: Array<{ key: string | null; body: string }>;
const id = 'synthetic-control-sheet';

beforeEach(async () => {
  vi.resetModules(); sessionStorage.clear(); localStorage.clear();
  sessionStorage.setItem('pkuba:scoresheet-reader:web-client-id', 'synthetic-control-client');
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  publicationCount = 0; detailAvailable = true; leaseActive = false; original = undefined; writes = [];
  const draft = makeDocument(id); draft.revision = 6; draft.status = 'validated';
  const raw = { id, game: {}, source: { id: 'synthetic-source', version: 1, url: '/synthetic.png', filename: 'synthetic.png', mime_type: 'image/png', width: 100, height: 100 },
    draft, draft_version: 6, event_sequence: 10, status: 'READY', validation_report: { errors: [], warnings: [] },
    validation_draft_version: 6, acknowledged_warnings: [], recognition: null,
    publication: null as null | { id: string; publication_number: number; draft_version: number; source_asset_id: string }, lease: null, can_upload_source: true };
  // Real App -> TopBar -> store -> API; only HTTP is synthetic. No database or model.
  vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, options: RequestInit = {}) => {
    const pathname = new URL(String(input), 'http://synthetic.invalid').pathname;
    const route = pathname === `/api/v1/scoresheets/${id}` ? 'detail' : pathname.replace(`/api/v1/scoresheets/${id}/`, '');
    if (route === 'detail') return detailAvailable ? response(raw) : response({ message: '合成：查回暂时失败' }, 503);
    if (route === 'lease') {
      if (raw.publication) return response({ read_only: true, read_only_reason: '已发布；普通管理员只读。', lease_token: null, holder: null });
      leaseActive = true;
      return response({ read_only: false, read_only_reason: '', lease_token: 'synthetic-lease', holder: null });
    }
    if (route === 'lease/heartbeat') return leaseActive ? response({ read_only: false, holder: null }) : response({ code: 'LEASE_REQUIRED', message: '租约已失效。' }, 409);
    if (route === 'validate') { expect(leaseActive).toBe(true); raw.event_sequence++; return response(raw); }
    if (route === 'publish') {
      const command = { key: new Headers(options.headers).get('Idempotency-Key'), body: String(options.body) };
      writes.push(command);
      if (original) { expect(command).toEqual(original); return response(raw); }
      original = command; publicationCount++; leaseActive = false;
      raw.status = 'PUBLISHED'; raw.event_sequence++; raw.can_upload_source = false;
      raw.publication = { id: 'synthetic-publication', publication_number: 1, draft_version: 6, source_asset_id: raw.source.id };
      detailAvailable = false;
      throw new TypeError('SYNTHETIC_PUBLICATION_RESPONSE_LOSS');
    }
    if (route === 'sync') return response({ current_version: 6, current_event: raw.event_sequence, requires_full_reload: false,
      events: [{ event_sequence: raw.event_sequence, type: 'PUBLISHED', payload: { publication_number: 1 } }], lease: null, recognition: null, can_upload_source: false });
    throw new Error('UNEXPECTED_SYNTHETIC_ROUTE ' + route);
  }));
  ({ api } = await import('./api'));
  ({ useEditorStore: store } = await import('./store'));
  ({ default: App } = await import('./App'));
  vi.spyOn(api, 'changes').mockResolvedValue({ items: [], next_before_id: null });
  vi.spyOn(api, 'games').mockResolvedValue({ items: [], total: 0, page: 1, page_size: 20, division_names: [] });
  await api.acquire(id);
  store.setState({ ...store.getInitialState(), document: structuredClone(draft), serverRevision: 6,
    template: makeTemplate(), loading: false, dirty: false, saveState: 'saved', online: true,
    initialize: vi.fn(), releaseLease: vi.fn().mockResolvedValue(undefined) });
});

it('offers the original submission check after event-only sync makes the editor read-only', async () => {
  render(<App />);
  fireEvent.click(screen.getByRole('button', { name: '提交记录表' }));
  await waitFor(() => expect(store.getState().error).toContain('SYNTHETIC_PUBLICATION_RESPONSE_LOSS'));
  expect(publicationCount).toBe(1); expect(api.hasPendingPublication(id, 6)).toBe(true);
  detailAvailable = true;
  await act(async () => { await store.getState().syncNow(); });
  expect(store.getState().readOnly).toBe(true);
  expect(store.getState().serverRevision).toBe(6);
  expect(screen.getByRole('button', { name: '保存草稿' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '校验' })).toBeDisabled();
  const recovery = screen.getByRole('button', { name: '核对原提交' });
  expect(recovery).toBeEnabled();
  fireEvent.click(recovery);
  await waitFor(() => expect(store.getState().document?.status).toBe('confirmed'));
  expect(api.hasPendingPublication(id, 6)).toBe(false);
  expect(writes).toHaveLength(2); expect(writes[1]).toEqual(writes[0]);
  expect(publicationCount).toBe(1); expect(store.getState().readOnly).toBe(true);
  expect(screen.getByRole('button', { name: '已提交' })).toBeDisabled();
});

it('does not enable submission for a read-only document without a pending command', () => {
  store.setState({ readOnly: true });
  render(<App />);
  const submit = screen.getByRole('button', { name: '提交记录表' });
  expect(submit).toBeDisabled(); fireEvent.click(submit);
  expect(screen.queryByRole('button', { name: '核对原提交' })).not.toBeInTheDocument();
  expect(writes).toHaveLength(0);
});
