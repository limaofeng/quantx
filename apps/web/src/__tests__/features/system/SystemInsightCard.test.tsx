import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SystemInsightCard } from '@/features/system/components/SystemInsightCard';

describe('SystemInsightCard', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('groups platform and runtime health including the market gateway', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        components: {
          api: { status: 'ready' },
          database: { status: 'ready', version: '16.11' },
          engine: { status: 'ready' },
          worker: { status: 'ready', onlineWorkers: 1 },
          prefect: { status: 'ready' },
          marketGateway: {
            status: 'ready',
            dependencies: { redis: 'ready' },
          },
          marketData: { status: 'ready', connectedDevices: 1 },
          qmtAgent: {
            status: 'ready',
            connectedDevices: 1,
            readyDevices: 1,
          },
          aiRuntime: { status: 'ready', configVersion: 4 },
        },
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<SystemInsightCard />);

    await waitFor(() => {
      expect(screen.getByText('系统运行正常')).toBeInTheDocument();
    });
    expect(
      screen.getByRole('heading', { name: '平台服务' })
    ).toBeInTheDocument();
    expect(
      screen.getByRole('heading', { name: '执行与运行时' })
    ).toBeInTheDocument();
    expect(screen.getByText('Market Gateway')).toBeInTheDocument();
    expect(screen.getByText('Redis Ready')).toBeInTheDocument();
    expect(
      screen.getByLabelText('Market Gateway: healthy')
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith('/health/components', {
      cache: 'no-store',
    });
  });
});
