// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
vi.hoisted(() => { vi.stubGlobal('PKUBA_API_BASE_URL', 'https://synthetic.invalid'); });
const f = vi.hoisted(() => ({ upload: vi.fn(), filename: '/synthetic/first.png', detail: null as any, collection: null as any, mode: 'network' }));
vi.mock('@tarojs/components', () => ({ View: ({ children, className }: any) => <div className={className}>{children}</div>, Text: ({ children }: any) => <span>{children}</span>, Image: () => <span />, Button: ({ children, className, disabled, onClick }: any) => <button className={className} disabled={disabled} onClick={onClick}>{children}</button> }));
vi.mock('@tarojs/taro', async () => {
  const react = await import('react');
  return { default: { uploadFile: f.upload, request: vi.fn(() => { throw new Error('LIVE_NETWORK_FORBIDDEN'); }), showModal: vi.fn(async () => ({ confirm: true })), showToast: vi.fn(), chooseMedia: vi.fn(async () => ({ tempFiles: [{ tempFilePath: f.filename }] })), previewImage: vi.fn() }, useRouter: () => ({ params: { id: 'synthetic-game' } }), useDidShow: (cb: () => void) => react.useEffect(() => { cb(); }, []) };
});
vi.mock('../../auth', () => ({ getMiniAppSession: () => 'synthetic-session', resolveMiniAppIdentity: async () => ({ token: 'synthetic-session', me: { admin_role: 'SUPERADMIN', leader_binding: null } }) }));
vi.mock('../../api', async importOriginal => {
  const actual = await importOriginal<any>();
  return { ...actual, api: { getGameDetail: async () => structuredClone(f.detail), getGameMedia: async () => structuredClone(f.collection) } };
});
import Page from './index';
let root: Root, container: HTMLDivElement;
const keys = () => f.upload.mock.calls.map(c => c[0].header['Idempotency-Key']);
async function flush() { await act(async () => { for (let n = 0; n < 16; n++) await Promise.resolve(); }); }
async function mount() { await act(async () => root.render(<Page />)); await flush(); }
async function replace() { const target = container.querySelector<HTMLButtonElement>('.media-replace'); expect(Boolean(target)).toBe(true); await act(async () => target!.click()); await flush(); }
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true; f.filename = '/synthetic/first.png'; f.mode = 'network';
  f.detail = { game: { id: 'synthetic-game', date: '2026-09-01', start_time: '18:00', division_name: '合成组', home_name: '合成甲', away_name: '合成乙', home_score: null, away_score: null, venue_name: '合成场地' }, group_photos: [], stats: null };
  f.collection = { assets: [{ id: 'synthetic-source', version: 2, kind: 'SCORESHEET', storage_status: 'ONLINE', content_url: '/synthetic.png', can_replace: true, can_delete: false }], can_upload: true };
  f.upload.mockReset().mockImplementation((o: any) => { if (f.mode === 'body') o.success({ statusCode: 200, data: '{' }); else o.fail({ errMsg: 'uploadFile:fail timeout' }); return { onProgressUpdate() {} }; });
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
describe('real general Mini media page + real upload wrapper', () => {
  it.each(['network', 'body'])('same current asset and selected path after %s loss keeps its explicit key', async mode => {
    f.mode = mode; await mount(); await replace(); await replace();
    expect(keys().length).toBe(mode === 'network' ? 4 : 2); expect(new Set(keys()).size).toBe(1);
  });
  it('different selected path is a distinct operation key', async () => {
    await mount(); await replace(); f.filename = '/synthetic/other.png'; await replace();
    expect(keys().length).toBe(4); expect(keys()[2] !== keys()[0]).toBe(true);
  });
  it('same logical retry after ordinary page reentry retains key', async () => {
    await mount(); await replace(); await act(async () => root.unmount()); root = createRoot(container);
    await mount(); await replace(); expect(keys().length).toBe(4); expect(keys()[2] === keys()[0]).toBe(true);
  });
});
