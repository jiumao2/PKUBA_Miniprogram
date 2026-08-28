// @vitest-environment jsdom
import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { beforeEach, afterEach, describe, it, expect, vi } from 'vitest';
import { createPkubaClient } from '@pkuba/api-client';

// Real page and shared client. The transport is an in-memory journal,
// not Django/PG; counts below are fixture observations, never database evidence.
const f = vi.hoisted(() => ({ api: {} as any, modal: vi.fn(), toast: vi.fn(), redirect: vi.fn() }));
vi.mock('../../api', () => ({ api: f.api }));
vi.mock('../../auth', () => ({ getMiniAppSession: () => 'synthetic-session' }));
vi.mock('@tarojs/components', () => ({
  View: ({ children, className }: any) => <div className={className}>{children}</div>,
  Text: ({ children }: any) => <span>{children}</span>,
  Button: ({ children, disabled, onClick }: any) => <button disabled={disabled} onClick={onClick}>{children}</button>,
  Picker: ({ children, range, value, onChange }: any) => <div>{children}<select value={value} onChange={e => onChange({ detail: { value: e.target.value } })}>{range.map((label: string, n: number) => <option key={n} value={n}>{label}</option>)}</select></div>,
}));
vi.mock('@tarojs/taro', async () => {
  const react = await import('react');
  return { default: { showModal: f.modal, showToast: f.toast, redirectTo: f.redirect, setNavigationBarTitle: vi.fn(), navigateTo: vi.fn() },
    useRouter: () => ({ params: { mode: 'same_week' } }),
    useDidShow: (callback: () => void) => react.useEffect(() => { callback(); }, []),
  };
});
import Page from './index';

let root: Root, container: HTMLDivElement;
let writes: Array<{ key: string; body: any }>;
let accepted: Map<string, any>;
let mode: 'success' | 'unknown' | 'unknown-committed';
let showAcceptedGame: boolean;
const game = { id: 'synthetic-game', version: 5, date: '2026-09-07', start_time: '18:00', venue_name: '合成场地', home_name: '合成甲', away_name: '合成乙', division_gender: 'MEN' };
const targets = [1, 2].map(n => ({ date: '2026-09-08', start_time: `1${n}:00`, period_id: `synthetic-period-${n}`, request_type: 'SAME_WEEK', request_type_label: '同周', process_route: 'ORDINARY', process_route_label: '普通流程' }));
async function flush() { await act(async () => { for (let n = 0; n < 12; n++) await Promise.resolve(); }); }
async function click(label: string) {
  const target = [...container.querySelectorAll('button')].find(b => b.textContent === label);
  expect(Boolean(target)).toBe(true);
  await act(async () => target!.click()); await flush();
}
async function mount() { await act(async () => root.render(<Page />)); await flush(); }
beforeEach(() => {
  (globalThis as any).IS_REACT_ACT_ENVIRONMENT = true;
  writes = []; accepted = new Map(); mode = 'unknown'; showAcceptedGame = true;
  f.modal.mockReset().mockResolvedValue({ confirm: true }); f.toast.mockReset(); f.redirect.mockReset().mockResolvedValue({});
  Object.assign(f.api, createPkubaClient('http://synthetic.invalid', async (url, options = {}): Promise<{ status: number; data: any }> => {
    const path = new URL(url).pathname;
    if (path.endsWith('/reschedule-requests/eligible-games')) return { status: 200, data: showAcceptedGame || !accepted.size ? [game] : [] };
    if (path.includes('/reschedule-requests/games/') && path.endsWith('/targets')) return { status: 200, data: targets };
    if (path.endsWith('/reschedule-requests/') && options.method === 'POST') {
      const key = options.headers!['Idempotency-Key'];
      const body = JSON.parse(options.body!); writes.push({ key, body });
      if (accepted.has(key)) return { status: 201, data: accepted.get(key) };
      if (accepted.size) return { status: 409, data: { code: 'VERSION_CONFLICT', message: '赛程已被其他操作更新，请刷新后重试。' } };
      const result = { id: 'synthetic-request', status: 'PENDING_OPPONENT' };
      if (mode === 'success') { accepted.set(key, result); return { status: 201, data: result }; }
      if (mode === 'unknown-committed') accepted.set(key, result);
      throw new Error('request:fail timeout');
    }
    throw new Error(`UNEXPECTED_MOCK_ROUTE ${path}`);
  }));
  container = document.createElement('div'); document.body.append(container); root = createRoot(container);
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });

describe('reschedule real page + real shared client', () => {
  it('known success shows submitted and redirects; cancel sends zero commands', async () => {
    mode = 'success'; await mount(); f.modal.mockResolvedValueOnce({ confirm: false });
    await click('提交申请'); expect(writes.length).toBe(0);
    await click('提交申请'); expect(writes.length).toBe(1);
    expect(f.toast).toHaveBeenCalledWith(expect.objectContaining({ title: '申请已提交' }));
    expect(f.redirect).toHaveBeenCalledWith({ url: '/pages/reschedule-requests/index' });
  });
  it('same intent after unknown response retains original idempotency key', async () => {
    await mount(); await click('提交申请'); await click('提交申请');
    expect(writes.length).toBe(2);
    expect(JSON.stringify(writes[0].body) === JSON.stringify(writes[1].body)).toBe(true);
    expect(writes[1].key === writes[0].key).toBe(true);
  });
  it('committed-response-loss retry recovers the known request with the original key', async () => {
    mode = 'unknown-committed'; await mount(); await click('提交申请');
    expect(accepted.size).toBe(1); expect(f.toast).not.toHaveBeenCalled();
    expect(container.textContent).toContain('request:fail timeout');
    await click('提交申请'); expect(writes.length).toBe(2);
    expect(writes[1].key === writes[0].key).toBe(true);
  });
  it('changed target is a different intent and gets a new key', async () => {
    await mount(); await click('提交申请');
    const select = container.querySelectorAll('select')[2];
    await act(async () => { select.value = '1'; select.dispatchEvent(new Event('change', { bubbles: true })); });
    await click('提交申请'); expect(writes.length).toBe(2);
    expect(writes[1].body.target_period_id !== writes[0].body.target_period_id).toBe(true);
    expect(writes[1].key !== writes[0].key).toBe(true);
  });
  it('same intent after error then ordinary page reentry retains the operation key', async () => {
    await mount(); await click('提交申请');
    await act(async () => root.unmount()); root = createRoot(container);
    await mount(); await click('提交申请');
    expect(writes.length).toBe(2);
    expect(JSON.stringify(writes[0].body) === JSON.stringify(writes[1].body)).toBe(true);
    expect(writes[1].key === writes[0].key).toBe(true);
  });
  it('offers explicit recovery of the original request even after accepted creation removes eligibility', async () => {
    mode = 'unknown-committed'; showAcceptedGame = false;
    await mount(); await click('提交申请'); await click('重新加载');
    expect(accepted.size).toBe(1); expect(writes.length).toBe(1);
    expect(container.textContent).toContain('当前没有满足政策和截止时间的可调比赛');
    expect(f.toast).not.toHaveBeenCalled(); expect(f.redirect).not.toHaveBeenCalled();
    await click('核对上次申请');
    expect(writes).toHaveLength(2);
    expect(writes[1].key).toBe(writes[0].key);
    expect(writes[1].body).toEqual(writes[0].body);
    expect(accepted.size).toBe(1);
    expect(f.toast).toHaveBeenCalledWith(expect.objectContaining({ title: '申请已提交' }));
    expect(f.redirect).toHaveBeenCalledWith({ url: '/pages/reschedule-requests/index' });
  });
});
