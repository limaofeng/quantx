import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SystemInsightCard } from '@/features/system/components/SystemInsightCard';

vi.mock('@/components/studio-workspace', () => ({
  useStudioNavigate: () => vi.fn(),
}));

describe('SystemInsightCard', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('summarizes the independent monitor status', async () => {
    const now = new Date().toISOString();
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({
        generatedAt: now,
        lastCycleAt: now,
        window: '24h',
        checkIntervalSeconds: 30,
        overallStatus: 'healthy',
        groups: [
          {
            id: 'external_dependency',
            name: '外部依赖',
            status: 'healthy',
            targetIds: ['postgresql'],
          },
          {
            id: 'quantx_runtime',
            name: 'QuantX 运行组件',
            status: 'healthy',
            targetIds: ['api-public'],
          },
        ],
        targets: [
          {
            id: 'postgresql',
            name: 'PostgreSQL',
            group: 'external_dependency',
            optional: false,
            derived: false,
            status: 'healthy',
            checkedAt: now,
            lastSuccessAt: now,
            latencyMs: 2.5,
            reasonCode: null,
            availabilityPct: 100,
            healthyPct: 100,
            coveragePct: 100,
            latencyP50Ms: 2.1,
            latencyP95Ms: 3.2,
            sampleCount: 120,
            activeIncident: false,
          },
          {
            id: 'api-public',
            name: 'API 公共入口',
            group: 'quantx_runtime',
            optional: false,
            derived: false,
            status: 'healthy',
            checkedAt: now,
            lastSuccessAt: now,
            latencyMs: 8.5,
            reasonCode: null,
            availabilityPct: 100,
            healthyPct: 100,
            coveragePct: 100,
            latencyP50Ms: 7.1,
            latencyP95Ms: 9.2,
            sampleCount: 120,
            activeIncident: false,
          },
        ],
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(<SystemInsightCard />);

    await waitFor(() => {
      expect(screen.getByText('系统观测状态：正常')).toBeInTheDocument();
    });
    expect(screen.getByText('外部依赖 · 正常')).toBeInTheDocument();
    expect(screen.getByText('QuantX 运行组件 · 正常')).toBeInTheDocument();
    expect(
      screen.getByRole('button', { name: '查看历史' })
    ).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith(
      '/monitor/api/v1/summary?window=24h',
      {
        cache: 'no-store',
        signal: undefined,
      }
    );
  });
});
