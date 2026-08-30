import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ServiceStatusPanel } from '@/features/settings/components/ServiceStatusPanel';
import type { MonitorSummary } from '@/features/system/monitor-api';

const monitorMocks = vi.hoisted(() => ({
  getMonitorSummary: vi.fn(),
  getMonitorHistory: vi.fn(),
  getMonitorIncidents: vi.fn(),
}));

vi.mock('@/features/system/monitor-api', () => monitorMocks);

function summary(
  qmtStatus: 'healthy' | 'degraded' | 'unavailable' = 'healthy'
): MonitorSummary {
  const now = new Date().toISOString();
  return {
    generatedAt: now,
    lastCycleAt: now,
    window: '24h' as const,
    checkIntervalSeconds: 30,
    overallStatus: qmtStatus,
    groups: [
      {
        id: 'quantx_runtime' as const,
        name: 'QuantX 运行组件',
        status: qmtStatus,
        targetIds: ['qmt-agent', 'engine'],
      },
    ],
    targets: [
      {
        id: 'qmt-agent',
        name: 'QMT Agent',
        group: 'quantx_runtime' as const,
        optional: false,
        probeKind: 'composite' as const,
        status: qmtStatus,
        checkedAt: now,
        lastSuccessAt: now,
        latencyMs: qmtStatus === 'healthy' ? 12.4 : 15.6,
        reasonCode: qmtStatus === 'healthy' ? null : 'QMT_AGENT_NOT_RECONCILED',
        availabilityPct: 99.5,
        healthyPct: 98.5,
        coveragePct: 100,
        latencyP50Ms: 11.2,
        latencyP95Ms: 18.8,
        sampleCount: 120,
        activeIncident: qmtStatus === 'unavailable',
      },
      {
        id: 'engine',
        name: '策略引擎',
        group: 'quantx_runtime' as const,
        optional: false,
        probeKind: 'derived' as const,
        status: 'healthy' as const,
        checkedAt: now,
        lastSuccessAt: now,
        latencyMs: null,
        reasonCode: null,
        availabilityPct: 100,
        healthyPct: 100,
        coveragePct: 100,
        latencyP50Ms: null,
        latencyP95Ms: null,
        sampleCount: 120,
        activeIncident: false,
      },
    ],
  };
}

describe('ServiceStatusPanel', () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it('shows composite QMT RTT, percentiles, trend evidence, and derived N/A', async () => {
    const now = new Date().toISOString();
    monitorMocks.getMonitorSummary.mockResolvedValue(summary());
    monitorMocks.getMonitorHistory.mockImplementation((targetId: string) =>
      Promise.resolve({
        target: { id: targetId, name: targetId },
        range: '24h',
        bucketSeconds: 60,
        points:
          targetId === 'qmt-agent'
            ? [
                {
                  start: now,
                  status: 'healthy',
                  sampleCount: 1,
                  healthyCount: 1,
                  degradedCount: 0,
                  unavailableCount: 0,
                  unknownCount: 0,
                  disabledCount: 0,
                  latencyCount: 0,
                  latencyMaxMs: null,
                  latencyP50Ms: null,
                  latencyP95Ms: null,
                },
                {
                  start: new Date(Date.now() + 60000).toISOString(),
                  status: 'healthy',
                  sampleCount: 1,
                  healthyCount: 1,
                  degradedCount: 0,
                  unavailableCount: 0,
                  unknownCount: 0,
                  disabledCount: 0,
                  latencyCount: 1,
                  latencyMaxMs: 20,
                  latencyP50Ms: 12,
                  latencyP95Ms: 20,
                },
              ]
            : [],
      })
    );
    monitorMocks.getMonitorIncidents.mockResolvedValue([]);

    render(<ServiceStatusPanel />);

    expect(await screen.findByText('延迟 12.40 ms')).toBeInTheDocument();
    expect(
      await screen.findByText(/状态综合 Windows 健康端点与服务端会话\/对账语义/)
    ).toBeInTheDocument();
    expect(screen.getByText('11.20 ms')).toBeInTheDocument();
    expect(screen.getByText('18.80 ms')).toBeInTheDocument();
    expect(
      await screen.findByRole('img', {
        name: /QMT Agent 历史状态：2 个时间段，正常 2/,
      })
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /QMT Agent/ })).toHaveAttribute(
      'aria-expanded',
      'true'
    );
    expect(screen.queryByText('延迟 0.00 ms')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /策略引擎/ }));

    await waitFor(() => {
      expect(
        screen.getByText('该组件来自语义快照，不生成虚假的独立延迟。')
      ).toBeInTheDocument();
    });
    expect(screen.getByRole('button', { name: /策略引擎/ })).toHaveAttribute(
      'aria-expanded',
      'true'
    );
    expect(screen.getByText('延迟 N/A')).toBeInTheDocument();
  });

  it('keeps a valid QMT RTT visible when the combined status is unavailable', async () => {
    monitorMocks.getMonitorSummary.mockResolvedValue(summary('unavailable'));
    monitorMocks.getMonitorHistory.mockResolvedValue({
      target: { id: 'qmt-agent', name: 'QMT Agent' },
      range: '24h',
      bucketSeconds: 60,
      points: [],
    });
    monitorMocks.getMonitorIncidents.mockResolvedValue([]);

    render(<ServiceStatusPanel />);

    expect(await screen.findByText('延迟 15.60 ms')).toBeInTheDocument();
    expect(screen.getAllByText('不可用').length).toBeGreaterThan(0);
  });

  it('explains current and historical reason codes in plain language', async () => {
    const monitorSummary = summary('degraded');
    monitorSummary.targets[0].reasonCode = 'XTTRADING_UNAVAILABLE';
    monitorMocks.getMonitorSummary.mockResolvedValue(monitorSummary);
    monitorMocks.getMonitorHistory.mockResolvedValue({
      target: { id: 'qmt-agent', name: 'QMT Agent' },
      range: '24h',
      bucketSeconds: 60,
      points: [],
    });
    monitorMocks.getMonitorIncidents.mockResolvedValue([
      {
        id: 112,
        targetId: 'qmt-agent',
        targetName: 'QMT Agent',
        openedAt: new Date().toISOString(),
        resolvedAt: null,
        active: true,
        reasonCode: 'QMT_AGENT_NOT_RECONCILED',
      },
    ]);

    render(<ServiceStatusPanel />);

    expect(
      await screen.findByText('当前原因：MiniQMT 交易连接未就绪')
    ).toBeInTheDocument();
    expect(
      screen.getByText(/XTTrading 尚未连接，实盘交易当前不可用/)
    ).toBeInTheDocument();
    expect(
      await screen.findByText('QMT Agent 尚未完成账户对账')
    ).toBeInTheDocument();
    expect(
      screen.getByText(/账户资产、持仓、委托和成交快照完成对账前/)
    ).toBeInTheDocument();
    expect(screen.getByText('XTTRADING_UNAVAILABLE')).toBeInTheDocument();
    expect(screen.getByText('QMT_AGENT_NOT_RECONCILED')).toBeInTheDocument();
  });

  it('clarifies that account admission availability is not the gate result', async () => {
    const monitorSummary = summary();
    const now = new Date().toISOString();
    monitorSummary.groups[0].targetIds.push('account-safety-observer');
    monitorSummary.targets.push({
      id: 'account-safety-observer',
      name: '账户准入观测',
      group: 'quantx_runtime',
      optional: true,
      probeKind: 'direct',
      status: 'healthy',
      checkedAt: now,
      lastSuccessAt: now,
      latencyMs: 20,
      reasonCode: null,
      availabilityPct: 100,
      healthyPct: 100,
      coveragePct: 100,
      latencyP50Ms: 18,
      latencyP95Ms: 25,
      sampleCount: 120,
      activeIncident: false,
    });
    monitorMocks.getMonitorSummary.mockResolvedValue(monitorSummary);
    monitorMocks.getMonitorHistory.mockImplementation((targetId: string) =>
      Promise.resolve({
        target: { id: targetId, name: targetId },
        range: '24h',
        bucketSeconds: 60,
        points: [],
      })
    );
    monitorMocks.getMonitorIncidents.mockResolvedValue([]);

    render(<ServiceStatusPanel />);
    fireEvent.click(
      await screen.findByRole('button', { name: /账户准入观测/ })
    );

    expect(
      await screen.findByText(/该状态只表示 Monitor 能持续采集脱敏准入快照/)
    ).toBeInTheDocument();
  });

  it('announces an initial monitor failure and offers a retry', async () => {
    monitorMocks.getMonitorSummary.mockRejectedValue(
      new Error('monitor unavailable')
    );

    render(<ServiceStatusPanel />);

    expect(await screen.findByText('Monitor 当前不可访问')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('Monitor 当前不可访问');
    expect(screen.getByRole('button', { name: '重试' })).toBeInTheDocument();
  });

  it('shows the empty state when the monitor has no targets', async () => {
    const emptySummary = summary();
    emptySummary.groups = [];
    emptySummary.targets = [];
    monitorMocks.getMonitorSummary.mockResolvedValue(emptySummary);

    render(<ServiceStatusPanel />);

    expect(await screen.findByText('尚未配置监测目标')).toBeInTheDocument();
    expect(monitorMocks.getMonitorHistory).not.toHaveBeenCalled();
  });
});
