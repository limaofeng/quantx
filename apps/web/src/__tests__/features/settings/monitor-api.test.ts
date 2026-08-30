import { afterEach, describe, expect, it, vi } from 'vitest';

import { getMonitorIncidents } from '@/features/system/monitor-api';

describe('Monitor incident API', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('sends target, range, pagination and cutoff through the public endpoint', async () => {
    const payload = {
      range: '1y',
      page: 3,
      pageSize: 20,
      total: 41,
      incidents: [],
      asOf: '2026-08-30T12:00:00Z',
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) });
    vi.stubGlobal('fetch', fetchMock);
    const signal = new AbortController().signal;
    expect(
      await getMonitorIncidents('1y', 'qmt-agent', 3, 20, signal, payload.asOf)
    ).toEqual(payload);
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost');
    expect(url.pathname).toBe('/monitor/api/v1/incidents');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      range: '1y',
      targetId: 'qmt-agent',
      page: '3',
      pageSize: '20',
      asOf: payload.asOf,
    });
    expect(fetchMock.mock.calls[0][1]).toEqual({ cache: 'no-store', signal });
  });
  it('surfaces non-success responses', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({ ok: false, status: 503 })
    );
    await expect(getMonitorIncidents('24h', 'qmt-agent')).rejects.toThrow(
      '503'
    );
  });
});
