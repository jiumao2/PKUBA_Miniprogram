import { beforeEach, describe, it, expect, vi } from 'vitest';
import { installTransport, rawRun, response } from './test/operationTransport';
beforeEach(() => { vi.resetModules(); sessionStorage.clear(); sessionStorage.setItem('pkuba:scoresheet-reader:web-client-id', 'synthetic-web-client'); });
describe('actual Web recognition API adapter', () => {
  it.each(['network', '503'])('same revision after %s failure keeps the original key', async failure => {
    const { calls } = installTransport(async (index, id) => {
      if (index === 1) {
        if (failure === 'network') throw new TypeError('SYNTHETIC_TRANSPORT_LOSS');
        return response({ code: 'SYNTHETIC_BUSY', message: '模拟服务不可用' }, 503);
      }
      return response(rawRun(id));
    });
    const { api } = await import('./api');
    await expect(api.createRecognition('synthetic-recognition', 6, true)).rejects.toThrow();
    await api.createRecognition('synthetic-recognition', 6, true);
    expect(calls.length).toBe(2); expect(calls[1].key === calls[0].key).toBe(true);
  });
  // Do not retire an operation when only its response headers arrived.
  it('2xx headers followed by response.json rejection keeps the original key', async () => {
    const { calls } = installTransport(async (index, id) => index === 1
      ? ({ ok: true, status: 200, json: async () => { throw new SyntaxError('SYNTHETIC_2XX_BODY_LOSS'); } } as unknown as Response)
      : response(rawRun(id)));
    const { api } = await import('./api');
    await expect(api.createRecognition('synthetic-recognition', 6, true)).rejects.toThrow('SYNTHETIC_2XX_BODY_LOSS');
    await api.createRecognition('synthetic-recognition', 6, true);
    expect(calls.length).toBe(2); expect(calls[1].key === calls[0].key).toBe(true);
  });
  it('different saved revision creates a different operation key', async () => {
    const { calls } = installTransport(async (_index, _id) => { throw new TypeError('SYNTHETIC_TRANSPORT_LOSS'); });
    const { api } = await import('./api');
    await expect(api.createRecognition('synthetic-recognition', 6, true)).rejects.toThrow();
    await expect(api.createRecognition('synthetic-recognition', 7, true)).rejects.toThrow();
    expect(calls.length).toBe(2); expect(calls[1].key !== calls[0].key).toBe(true);
  });
  it('acknowledged success retires the key for a later operation', async () => {
    const { calls } = installTransport(async (_index, id) => response(rawRun(id)));
    const { api } = await import('./api');
    await api.createRecognition('synthetic-recognition', 6, true);
    await api.createRecognition('synthetic-recognition', 7, true);
    expect(calls.length).toBe(2); expect(calls[1].key !== calls[0].key).toBe(true);
  });
});
