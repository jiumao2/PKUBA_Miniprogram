// @vitest-environment jsdom
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { createPkubaClient } from '@pkuba/api-client';
vi.hoisted(() => { vi.stubGlobal('PKUBA_API_BASE_URL', 'https://synthetic.invalid'); });
const f = vi.hoisted(() => ({ upload: vi.fn(), request: vi.fn() }));
vi.mock('@tarojs/taro', () => ({ default: { uploadFile: f.upload, request: f.request } }));
import { uploadGameMedia, replaceGameMedia } from './api';
const invoke = (kind: string, key?: string, filename = '/synthetic/first.png') => kind === 'upload'
  ? uploadGameMedia('synthetic-game', filename, 'SCORESHEET', true, 'synthetic-session', undefined, key)
  : replaceGameMedia('synthetic-media', 2, filename, true, 'synthetic-session', undefined, key);
beforeEach(() => { f.upload.mockReset(); f.request.mockReset(); });
describe.each(['upload', 'replace'])('mini media %s real wrapper', kind => {
  it('native transport automatic retry preserves the key within one invocation', async () => {
    f.upload.mockImplementationOnce((o: any) => { o.fail({ errMsg: 'uploadFile:fail timeout' }); return { onProgressUpdate() {} }; });
    f.upload.mockImplementationOnce((o: any) => { o.success({ statusCode: 200, data: JSON.stringify({ id: 'synthetic-media' }) }); return { onProgressUpdate() {} }; });
    await invoke(kind); const calls = f.upload.mock.calls.map(c => c[0]);
    expect(calls.length).toBe(2); expect(calls[0].header['Idempotency-Key'] === calls[1].header['Idempotency-Key']).toBe(true);
  });
  it('same logical external retry after two transport losses preserves the key', async () => {
    f.upload.mockImplementation((o: any) => { o.fail({ errMsg: 'uploadFile:fail timeout' }); return { onProgressUpdate() {} }; });
    await expect(invoke(kind)).rejects.toThrow(); await expect(invoke(kind)).rejects.toThrow();
    const keys = f.upload.mock.calls.map(c => c[0].header['Idempotency-Key']);
    expect(keys.length).toBe(4); expect(keys[0] === keys[1] && keys[2] === keys[3]).toBe(true);
    expect(keys[2] === keys[0]).toBe(true);
  });
  it('2xx malformed/truncated body is not confirmed success and external retry retains key', async () => {
    f.upload.mockImplementation((o: any) => { o.success({ statusCode: 200, data: '{' }); return { onProgressUpdate() {} }; });
    await expect(invoke(kind)).rejects.toThrow('无法识别'); await expect(invoke(kind)).rejects.toThrow('无法识别');
    const keys = f.upload.mock.calls.map(c => c[0].header['Idempotency-Key']);
    expect(keys.length).toBe(2); expect(keys[1] === keys[0]).toBe(true);
  });
  it('explicit caller key survives external retry, while changed-file default operation gets a new key', async () => {
    f.upload.mockImplementation((o: any) => { o.fail({ errMsg: 'uploadFile:fail timeout' }); return { onProgressUpdate() {} }; });
    await expect(invoke(kind, 'synthetic-idempotency')).rejects.toThrow(); await expect(invoke(kind, 'synthetic-idempotency')).rejects.toThrow();
    let keys = f.upload.mock.calls.map(c => c[0].header['Idempotency-Key']);
    expect(new Set(keys).size).toBe(1);
    f.upload.mockClear(); await expect(invoke(kind)).rejects.toThrow(); await expect(invoke(kind, undefined, '/synthetic/second.png')).rejects.toThrow();
    keys = f.upload.mock.calls.map(c => c[0].header['Idempotency-Key']); expect(keys[2] !== keys[0]).toBe(true);
  });
});
it('shared client supports exact explicit key replay for create/publish/retry commands', async () => {
  const calls: any[] = [];
  const client = createPkubaClient('http://synthetic.invalid', async <T,>(_url: string, options: any) => { calls.push(options); return { status: 200, data: {} as T }; });
  const payload = { game_id: 'synthetic-game', expected_game_version: 2, target_date: '2026-09-08', target_period_id: 'synthetic-period', process_route: 'ORDINARY' } as any;
  const context = { expected_version: 2, client_id: 'synthetic-client', lease_token: 'synthetic-lease', surface: 'MINIAPP' } as any;
  await client.createRescheduleRequest(payload, 'synthetic-session', 'synthetic-key'); await client.createRescheduleRequest(payload, 'synthetic-session', 'synthetic-key');
  await client.publishScoresheet('synthetic-sheet', context, 'synthetic-session', 'synthetic-key'); await client.publishScoresheet('synthetic-sheet', context, 'synthetic-session', 'synthetic-key');
  await client.retryScoresheetRecognition('synthetic-sheet', { ...context, confirmed_overwrite: true }, 'synthetic-session', 'synthetic-key'); await client.retryScoresheetRecognition('synthetic-sheet', { ...context, confirmed_overwrite: true }, 'synthetic-session', 'synthetic-key');
  expect(calls.length).toBe(6); expect(new Set(calls.map(c => c.headers['Idempotency-Key'])).size).toBe(1);
});
