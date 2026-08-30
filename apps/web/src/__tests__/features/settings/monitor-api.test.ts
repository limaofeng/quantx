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
      maxIncidentId: 41,
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: () => Promise.resolve(payload) });
    vi.stubGlobal('fetch', fetchMock);
    const signal = new AbortController().signal;
    expect(
      await getMonitorIncidents('1y', 'qmt-agent', 3, 20, signal, {
        asOf: payload.asOf,
        maxIncidentId: payload.maxIncidentId,
      })
    ).toEqual(payload);
    const url = new URL(fetchMock.mock.calls[0][0], 'http://localhost');
    expect(url.pathname).toBe('/monitor/api/v1/incidents');
    expect(Object.fromEntries(url.searchParams)).toEqual({
      range: '1y',
      targetId: 'qmt-agent',
      page: '3',
      pageSize: '20',
      asOf: payload.asOf,
      maxIncidentId: '41',
    });
    expect(fetchMock.mock.calls[0][1]).toEqual({ cache: 'no-store', signal });
  });
  it('omits both snapshot fields for a new query and preserves a zero watermark', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue({ ok: true, json: () => Promise.resolve({}) });
    vi.stubGlobal('fetch', fetchMock);
    await getMonitorIncidents('24h', 'qmt-agent');
    let url = new URL(fetchMock.mock.calls.at(-1)![0], 'http://localhost');
    expect(url.searchParams.has('asOf')).toBe(false);
    expect(url.searchParams.has('maxIncidentId')).toBe(false);
    await getMonitorIncidents('24h', 'qmt-agent', 1, 20, undefined, {
      asOf: '2026-08-30T12:00:00Z',
      maxIncidentId: 0,
    });
    url = new URL(fetchMock.mock.calls.at(-1)![0], 'http://localhost');
    expect(url.searchParams.get('maxIncidentId')).toBe('0');
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
