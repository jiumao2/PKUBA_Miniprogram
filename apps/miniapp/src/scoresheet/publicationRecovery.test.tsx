// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { ApiError, createPkubaClient } from '@pkuba/api-client';
import { makeDocument } from '../../../admin-web/src/scoresheet-reader/test/fixtures';
// Actual editor + actual shared client for mutations. State/lease below model
// documented service branches; they are NOT a Django/PG execution or publication count.
const f = vi.hoisted(() => ({
  api: { getScoresheet: vi.fn(), acquireScoresheetLease: vi.fn(), heartbeatScoresheetLease: vi.fn(), releaseScoresheetLease: vi.fn(), syncScoresheet: vi.fn(), saveScoresheetDraft: vi.fn(), validateScoresheet: vi.fn(), publishScoresheet: vi.fn(), acknowledgeScoresheetWarnings: vi.fn(), retryScoresheetRecognition: vi.fn() },
  storage: new Map<string, any>(), modal: vi.fn(), toast: vi.fn(), server: null as any,
  holder: null as any, lease: '', leaseSequence: 0, role: 'SUPERADMIN',
  mode: 'loss-only', publications: 0,
  writes: [] as Array<{ kind: string; key: string; body: any }>,
}));
vi.mock('@tarojs/components', () => {
  const block = ({ children, className, id, onClick }: any) => <div className={className} id={id} onClick={onClick}>{children}</div>;
  return { View: block, Text: block, ScrollView: block, MovableArea: block, MovableView: block, Image: () => <span />,
    Button: ({ children, className, disabled, onClick }: any) => <button className={className} disabled={disabled} onClick={onClick}>{children}</button> };
});
vi.mock('@tarojs/taro', async () => {
  const react = await import('react');
  return { default: {
    getStorageSync: (key: string) => f.storage.get(key), setStorageSync: (key: string, value: any) => f.storage.set(key, structuredClone(value)), removeStorageSync: (key: string) => f.storage.delete(key),
    showModal: f.modal, showToast: f.toast, onNetworkStatusChange: vi.fn(), offNetworkStatusChange: vi.fn(),
    createSelectorQuery: () => ({ select: () => ({ boundingClientRect: (cb: any) => ({ exec: () => cb({ width: 390, height: 550 }) }) }) }),
  }, useRouter: () => ({ params: { id: 'synthetic-mini-sheet' } }), useDidShow: (cb: () => void) => react.useEffect(() => { cb(); }, []), useUnload: (cb: () => void) => react.useEffect(() => () => cb(), []) };
});
vi.mock('../api', () => ({ api: f.api, absoluteMediaUrl: (s: string) => s, replaceGameMedia: vi.fn() }));
vi.mock('../auth', () => ({ getMiniAppSession: () => 'synthetic-session' }));
vi.mock('./MobileStandardView', () => ({ MobileStandardView: ({ document, onChange, readOnly }: any) => <div><span data-crew>{document.header.crew_chief}</span><button disabled={readOnly} onClick={() => { const next = structuredClone(document); next.header.crew_chief = '合成人工修改'; onChange(next, true); }}>修改人工主裁</button></div> }));
import Editor from './pages/editor/index';
let container: HTMLDivElement, root: Root;
const clone = <T,>(v: T): T => structuredClone(v);
const button = (label: string) => [...container.querySelectorAll<HTMLButtonElement>('button')].find(b => b.textContent === label);
async function flush() { await act(async () => { for (let n = 0; n < 16; n++) await Promise.resolve(); await vi.advanceTimersByTimeAsync(0); }); }
async function click(target: HTMLElement | undefined) { expect(Boolean(target)).toBe(true); await act(async () => target!.click()); await flush(); }
async function mount() { await act(async () => root.render(<Editor />)); await flush(); }
async function publishPage() { await mount(); await click(button('标准表')); await click([...container.querySelectorAll<HTMLElement>('.mini-sheet-step')].find(e => e.textContent === '6发布')); }
async function tick() { await act(async () => vi.advanceTimersByTimeAsync(2000)); await flush(); }
const publishedWrites = () => f.writes.filter(w => w.kind === 'publish');
const retryWrites = () => f.writes.filter(w => w.kind === 'retry');
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true; vi.useFakeTimers();
  f.storage.clear(); f.storage.set('pkuba-scoresheet-miniapp-client', 'synthetic-mini-client');
  f.holder = null; f.lease = ''; f.leaseSequence = 0; f.publications = 0; f.writes = []; f.role = 'SUPERADMIN'; f.mode = 'loss-only';
  Object.values(f.api).forEach(fn => fn.mockReset()); f.modal.mockReset().mockResolvedValue({ confirm: true }); f.toast.mockReset();
  const draft = makeDocument('synthetic-mini-sheet'); draft.header.crew_chief = '合成人工原值';
  f.server = { id: draft.id, game: { id: 'synthetic-game', label: '合成甲 — 合成乙' }, source: { id: 'synthetic-source', version: 1, url: '/synthetic.png', filename: 'synthetic.png', width: 100, height: 100 }, source_version: 1,
    status: 'DRAFT', draft, draft_version: 5, event_sequence: 10, reviewed_regions: {}, validation_report: { errors: [], warnings: [] }, validation_draft_version: null, acknowledged_warnings: [],
    recognition: { id: 'synthetic-failed-run', status: 'FAILED', can_retry: true, attempt_count: 4, max_attempts: 4, next_attempt_at: null, model: 'MOCK_ONLY', prompt_version: 'MOCK_ONLY', image_sha256: 'synthetic', auto_apply_allowed: false, last_error_code: 'SYNTHETIC_FAILURE', last_error: '模拟失败' }, lease: null, publication: null };
  f.api.getScoresheet.mockImplementation(async () => clone(f.server));
  f.api.acquireScoresheetLease.mockImplementation(async () => {
    if (f.server.publication && f.role === 'ADMIN') return { read_only: true, read_only_reason: '已发布记录表的纠错和重新发布仅限超级管理员。', lease_token: null, holder: null };
    f.holder = { client_id: 'synthetic-mini-client', surface: 'MINIAPP', username: '合成管理员' }; f.lease = `synthetic-lease-${++f.leaseSequence}`;
    return { read_only: false, lease_token: f.lease, holder: f.holder };
  });
  f.api.releaseScoresheetLease.mockImplementation(async () => { f.holder = null; f.lease = ''; });
  f.api.heartbeatScoresheetLease.mockResolvedValue({});
  f.api.syncScoresheet.mockImplementation(async (_id, _version, event) => ({ current_version: f.server.draft_version, current_event: f.server.event_sequence, events: event < f.server.event_sequence ? [{ type: 'SYNTHETIC_EVENT' }] : [], requires_full_reload: false, lease: clone(f.holder) }));
  f.api.saveScoresheetDraft.mockImplementation(async (_id, _context, patches) => { f.server.draft = clone(patches[0].value); f.server.draft_version++; f.server.event_sequence++; f.server.status = 'DRAFT'; return clone(f.server); });
  f.api.validateScoresheet.mockImplementation(async (_id, context) => {
    if (f.server.publication && f.role === 'ADMIN') throw new ApiError('已发布记录表的纠错和重新发布仅限超级管理员。', 403, 'SUPERADMIN_REQUIRED');
    if (!f.holder || context.lease_token !== f.lease) throw new ApiError('模拟：编辑租约已失效。', 409, 'LEASE_REQUIRED');
    f.server.status = 'READY'; f.server.validation_draft_version = f.server.draft_version; f.server.event_sequence++; return clone(f.server);
  });
  const realClient = createPkubaClient('http://synthetic.invalid', async (url, options = {}): Promise<{ status: number; data: any }> => {
    const path = new URL(url).pathname; const kind = path.endsWith('/publish') ? 'publish' : path.endsWith('/recognition/retry') ? 'retry' : 'unexpected';
    if (kind === 'unexpected') throw new Error(`UNEXPECTED_MOCK_ROUTE ${path}`);
    const body = JSON.parse(options.body!); f.writes.push({ kind, key: options.headers!['Idempotency-Key'], body });
    if (kind === 'retry') throw new Error('request:fail timeout');
    if (f.mode === 'loss-only') throw new Error('request:fail timeout');
    f.publications++; f.server.publication = { id: 'synthetic-publication', publication_number: f.publications, draft_version: f.server.draft_version, source_asset_id: f.server.source.id }; f.server.status = 'PUBLISHED'; f.server.event_sequence++; f.holder = null; f.lease = '';
    if (f.mode === 'commit-loss') throw new Error('request:fail timeout');
    return { status: 200, data: clone(f.server) };
  });
  f.api.publishScoresheet.mockImplementation((...args: any[]) => (realClient.publishScoresheet as any)(...args));
  f.api.retryScoresheetRecognition.mockImplementation((...args: any[]) => (realClient.retryScoresheetRecognition as any)(...args));
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); vi.clearAllTimers(); vi.useRealTimers(); });
describe('real Mini editor mutation flow', () => {
  it('known publication success displays published without an automatic second command', async () => {
    f.mode = 'success'; await publishPage(); await click(button('校验并发布'));
    expect(f.toast).toHaveBeenCalledWith(expect.objectContaining({ title: '发布成功' }));
    expect(container.textContent).toContain('已发布'); expect(publishedWrites().length).toBe(1);
    await tick(); await tick(); expect(publishedWrites().length).toBe(1);
  });
  it('same unchanged publish intent after transport loss retains its key', async () => {
    await publishPage(); await click(button('校验并发布')); await click(button('校验并发布'));
    const calls = publishedWrites(); expect(calls.length).toBe(2);
    expect(JSON.stringify(calls[0].body) === JSON.stringify(calls[1].body)).toBe(true);
    expect(calls[1].key === calls[0].key).toBe(true);
  });
  it.each(['ADMIN', 'SUPERADMIN'])('after committed response loss %s recovers the authoritative publication without another write', async role => {
    f.role = role; f.mode = 'commit-loss'; await publishPage(); await click(button('校验并发布'));
    expect(f.publications).toBe(1);
    expect(f.toast).toHaveBeenCalledWith(expect.objectContaining({ title: '发布成功' }));
    expect(container.textContent).toContain('已发布');
    expect(container.textContent).not.toContain('request:fail timeout');
    await tick(); await tick();
    expect(publishedWrites().length).toBe(1);
  });
  it('recovered committed loss stays known through event-only sync and does not auto-republish', async () => {
    f.mode = 'commit-loss'; await publishPage(); await click(button('校验并发布'));
    await tick(); await tick();
    expect(button('重新识别')).toBeUndefined(); expect(publishedWrites().length).toBe(1); expect(f.publications).toBe(1);
    expect(container.textContent).not.toContain('request:fail timeout');
    expect(f.toast).toHaveBeenCalledWith(expect.objectContaining({ title: '发布成功' }));
  });
  it('retry recognition preserves key at the same saved version but changes it after an edit', async () => {
    await mount(); await click(button('重新识别')); await click(button('重新识别'));
    let calls = retryWrites(); expect(calls.length).toBe(2); expect(calls[1].key === calls[0].key).toBe(true);
    await click(button('标准表')); await click(button('修改人工主裁')); await click(button('重新识别'));
    calls = retryWrites(); expect(calls.length).toBe(3); expect(calls[2].body.expected_version).toBe(6);
    expect(calls[2].key !== calls[0].key).toBe(true); expect(f.server.draft.header.crew_chief).toBe('合成人工修改');
  });
  it('changed saved draft is a new publish intent and gets a new key', async () => {
    await publishPage(); await click(button('校验并发布')); await click(button('修改人工主裁')); await click(button('校验并发布'));
    const calls = publishedWrites(); expect(calls.length).toBe(2); expect(calls[1].body.expected_version).toBe(6); expect(calls[1].key !== calls[0].key).toBe(true);
  });
  it('clean page reentry after recognition result unknown retains the operation key', async () => {
    await mount(); await click(button('重新识别'));
    await act(async () => root.unmount()); root = createRoot(container);
    await mount(); await click(button('重新识别'));
    const calls = retryWrites(); expect(calls.length).toBe(2);
    expect(calls[1].body.expected_version === calls[0].body.expected_version).toBe(true);
    expect(calls[1].body.client_id === calls[0].body.client_id).toBe(true);
    expect(calls[1].key === calls[0].key).toBe(true);
  });
});
