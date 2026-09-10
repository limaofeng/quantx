import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { MarketSyncEvidence } from './MarketSyncEvidence';

vi.mock('@/core/auth', () => ({ getAccessToken: () => 'test-token' }));

const evidence = (status: string, count = 1) => ({
  counts: [{ coverage_status: 'PENDING', request_status: status, count }],
  items: [
    {
      batch_index: 1,
      request_id: 'request-1',
      coverage_status: 'PENDING',
      request_status: status,
      request_phase: null,
      request_reason: null,
      records_saved: status === 'COMPLETED' ? '5166' : null,
      scope: {
        stock_list: ['601318.SH'],
        periods: ['tick'],
        start_time: '20260803',
        end_time: '20260803',
      },
      summary: {},
    },
  ],
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('MarketSyncEvidence', () => {
  it('keeps observing requests after the Flow ends without claiming coverage success', async () => {
    vi.useFakeTimers();
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce({
        ok: true,
        json: async () => evidence('UPLOADED'),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => evidence('COMPLETED'),
      });
    vi.stubGlobal('fetch', fetchMock);
    await act(async () => {
      render(<MarketSyncEvidence runId="run" live={false} />);
    });
    expect(screen.getByText(/后台处理中 1/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15000);
    });
    expect(screen.getByText('已处理')).toBeInTheDocument();
    expect(screen.getByText('待校验')).toBeInTheDocument();
    expect(screen.getByText(/覆盖合格 0/)).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('pages evidence instead of downloading the complete run audit', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => evidence('COMPLETED', 75),
    });
    vi.stubGlobal('fetch', fetchMock);
    await act(async () => {
      render(<MarketSyncEvidence runId="run" live={false} />);
    });
    await act(async () => {
      fireEvent.click(screen.getByText('下一页'));
    });
    expect(fetchMock.mock.calls[1][0]).toContain('offset=50&limit=50');
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe(
      'Bearer test-token'
    );
  });
  it('shows blocked state and its reason without claiming background progress', async () => {
    const result = evidence('BLOCKED');
    const items = result.items.map(item => ({
      ...item,
      summary: { reason: 'DATA_UNAVAILABLE' },
      request_phase: 'READBACK',
      request_reason: 'READBACK_BUDGET_EXHAUSTED',
    }));
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        json: async () => ({ ...result, items }),
      })
    );
    await act(async () => {
      render(<MarketSyncEvidence runId="blocked" live={false} />);
    });
    expect(screen.getByText('已阻塞')).toBeInTheDocument();
    expect(screen.getByText('回读核验额度耗尽')).toBeInTheDocument();
    expect(screen.getByText('回读核验')).toBeInTheDocument();
    expect(screen.queryByText('源数据不可用')).not.toBeInTheDocument();
    expect(screen.getByText(/后台处理中 0/)).toBeInTheDocument();
  });
});
